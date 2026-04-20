from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import indicators
from clients.alpaca import AlpacaClient
from config.loader import load_settings
from config.schema import AppConfig
from journal import (
    apply_monotonic_stop,
    compute_open_profit,
    compute_profit_capture_pct,
    compute_stop_locked_profit,
    compute_used_stop,
    should_block_stop_relaxation,
)
from storage.sqlite import SQLiteStorage


DEFAULT_INITIAL_CAPITAL = 10_000.0
DEFAULT_RISK_PCT = 2.0
DEFAULT_LOOKBACK_YEARS = 3.0
DEFAULT_MAX_TOTAL_OPEN_RISK_PCT = 6.0
DEFAULT_MAX_OPEN_POSITIONS = 2
WATCHLIST_SESSION_LIMIT = 5
DEFAULT_BATCH_SIZE = 40
HOURLY_BATCH_SIZE = 5
HOURLY_WINDOW_DAYS = 120
WEEKLY_BUFFER_DAYS = 420
DAILY_BUFFER_DAYS = 120
HOURLY_BUFFER_DAYS = 21


@dataclass
class FrameBundle:
    frame: pd.DataFrame
    ordinals: np.ndarray


@dataclass
class SymbolHistory:
    symbol: str
    weekly: FrameBundle
    daily: FrameBundle
    hourly: FrameBundle


@dataclass
class CandidateRecord:
    symbol: str
    direction: str
    source_session_date: str


@dataclass(frozen=True)
class PositionSizing:
    shares: int
    used_risk: float
    position_cost: float
    allowed_risk: float


@dataclass
class Position:
    symbol: str
    direction: str
    entry_session_date: str
    entry_timestamp: str
    entry_price: float
    initial_stop: float
    active_stop: float
    risk_per_share: float
    shares: int
    take_profit: float
    source_session_date: str
    bars_held: int
    position_cost: float
    last_price: float
    entry_cash_before: float
    entry_equity_before: float
    entry_open_risk_before: float
    entry_remaining_stop_budget: float
    entry_allowed_risk: float


@dataclass
class TriggerEvent:
    candidate: CandidateRecord
    timestamp: pd.Timestamp
    current_bar: pd.Series
    exits: dict[str, Any]


@dataclass
class TradeResult:
    symbol: str
    direction: str
    source_session_date: str
    entry_session_date: str
    entry_timestamp: str
    exit_session_date: str
    exit_timestamp: str
    entry_price: float
    exit_price: float
    initial_stop: float
    final_stop: float
    shares: int
    pnl: float
    pnl_pct: float
    r_multiple: float
    bars_held: int
    exit_reason: str
    position_cost: float
    entry_cash_before: float
    entry_equity_before: float
    entry_open_risk_before: float
    entry_remaining_stop_budget: float
    entry_allowed_risk: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest the triple-screen system over historical data.")
    parser.add_argument("--years", type=float, default=DEFAULT_LOOKBACK_YEARS, help="Lookback years ending today.")
    parser.add_argument("--start", type=str, default="", help="Backtest start date (YYYY-MM-DD).")
    parser.add_argument("--end", type=str, default="", help="Backtest end date (YYYY-MM-DD).")
    parser.add_argument("--risk-pct", type=float, default=DEFAULT_RISK_PCT, help="Account risk per trade as a percentage.")
    parser.add_argument(
        "--max-total-open-risk-pct",
        type=float,
        default=DEFAULT_MAX_TOTAL_OPEN_RISK_PCT,
        help="Cap on total open-position stop risk as a percentage of current account equity.",
    )
    parser.add_argument(
        "--max-open-positions",
        type=int,
        default=DEFAULT_MAX_OPEN_POSITIONS,
        help="Maximum concurrent open positions allowed by the stop-budget model.",
    )
    parser.add_argument("--initial-capital", type=float, default=DEFAULT_INITIAL_CAPITAL, help="Starting account equity.")
    parser.add_argument(
        "--initial-buying-power",
        type=float,
        default=0.0,
        help="Optional starting tradable capital. When greater than initial capital, the backtest allows financing/margin.",
    )
    parser.add_argument("--max-symbols", type=int, default=0, help="Optional universe cap for faster experiments.")
    parser.add_argument("--output", type=str, default="", help="Optional JSON output path.")
    return parser


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def timeframe_batch_size(timeframe: str) -> int:
    if timeframe == "hour":
        return HOURLY_BATCH_SIZE
    return DEFAULT_BATCH_SIZE


def timeframe_window_starts(
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    if timeframe != "hour":
        return [(start, end)]

    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + timedelta(days=HOURLY_WINDOW_DAYS), end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def merge_frames(existing: pd.DataFrame | None, incoming: pd.DataFrame | None) -> pd.DataFrame:
    if existing is None or existing.empty:
        return ensure_utc_naive(incoming)
    if incoming is None or incoming.empty:
        return ensure_utc_naive(existing)

    merged = pd.concat([ensure_utc_naive(existing), ensure_utc_naive(incoming)])
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()


def ensure_utc_naive(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    result = frame.copy()
    index = pd.DatetimeIndex(result.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")
    result.index = index.tz_localize(None)
    return result.sort_index()


def trim_frame_to_window(frame: pd.DataFrame | None, start: datetime, end: datetime) -> pd.DataFrame:
    prepared = ensure_utc_naive(frame)
    if prepared.empty:
        return prepared
    start_value = pd.Timestamp(start).tz_convert("UTC").tz_localize(None)
    end_value = pd.Timestamp(end).tz_convert("UTC").tz_localize(None)
    return prepared.loc[(prepared.index >= start_value) & (prepared.index < end_value)]


def timeframe_coverage_delta(timeframe: str) -> timedelta:
    if timeframe == "week":
        return timedelta(days=7)
    if timeframe == "day":
        return timedelta(days=1)
    return timedelta(hours=1)


def has_full_coverage(frame: pd.DataFrame | None, timeframe: str, start: datetime, end: datetime) -> bool:
    prepared = trim_frame_to_window(frame, start, end)
    if prepared.empty:
        return False
    start_value = pd.Timestamp(start).tz_convert("UTC").tz_localize(None)
    end_cutoff = pd.Timestamp(end - timeframe_coverage_delta(timeframe)).tz_convert("UTC").tz_localize(None)
    return prepared.index.min() <= start_value and prepared.index.max() >= end_cutoff


def to_bundle(frame: pd.DataFrame, market_timezone: ZoneInfo) -> FrameBundle:
    frame = ensure_utc_naive(frame)
    if frame.empty:
        return FrameBundle(frame=frame, ordinals=np.array([], dtype=np.int32))
    local_index = pd.DatetimeIndex(frame.index).tz_localize("UTC").tz_convert(market_timezone)
    ordinals = np.array([item.date().toordinal() for item in local_index], dtype=np.int32)
    return FrameBundle(frame=frame, ordinals=ordinals)


def slice_to_session(bundle: FrameBundle, session_date: date) -> pd.DataFrame:
    if bundle.frame.empty:
        return bundle.frame
    cutoff = session_date.toordinal()
    position = int(np.searchsorted(bundle.ordinals, cutoff, side="right"))
    return bundle.frame.iloc[:position]


def slice_session_hours(bundle: FrameBundle, session_date: date) -> pd.DataFrame:
    if bundle.frame.empty:
        return bundle.frame
    start = session_date.toordinal()
    end = (session_date + timedelta(days=1)).toordinal()
    left = int(np.searchsorted(bundle.ordinals, start, side="left"))
    right = int(np.searchsorted(bundle.ordinals, end, side="left"))
    return bundle.frame.iloc[left:right]


def parse_date_arg(raw: str) -> date | None:
    text = raw.strip()
    if not text:
        return None
    return date.fromisoformat(text)


def resolve_period(args: argparse.Namespace) -> tuple[date, date]:
    end_date = parse_date_arg(args.end) or datetime.now(UTC).date()
    start_date = parse_date_arg(args.start)
    if start_date is None:
        lookback_days = max(1, int(round(args.years * 365.25)))
        start_date = end_date - timedelta(days=lookback_days)
    if start_date >= end_date:
        raise ValueError("Backtest start date must be earlier than end date.")
    return start_date, end_date


def fetch_timeframe_history(
    market_data: AlpacaClient,
    storage: SQLiteStorage | None,
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
) -> dict[str, pd.DataFrame]:
    api_timeframe = {"week": "1Week", "day": "1Day", "hour": "1Hour"}[timeframe]
    merged: dict[str, pd.DataFrame] = {}
    missing_symbols: list[str] = []

    for symbol in symbols:
        cached = storage.get_price_bars(symbol, timeframe) if storage else None
        cached_window = trim_frame_to_window(cached, start, end)
        if has_full_coverage(cached_window, timeframe, start, end):
            merged[symbol] = cached_window
        else:
            if not cached_window.empty:
                merged[symbol] = cached_window
            missing_symbols.append(symbol)

    if not missing_symbols:
        print(f"[cache] timeframe={timeframe} source=sqlite symbols={len(symbols)}", flush=True)
        return merged

    chunks = chunked(missing_symbols, timeframe_batch_size(timeframe))
    windows = timeframe_window_starts(timeframe, start, end)
    for chunk_index, symbol_chunk in enumerate(chunks, start=1):
        for window_index, (window_start, window_end) in enumerate(windows, start=1):
            frames = market_data.fetch_bars_batch(symbol_chunk, api_timeframe, window_start, window_end)
            for symbol in symbol_chunk:
                frame = trim_frame_to_window(frames.get(symbol), start, end)
                if storage and not frame.empty:
                    storage.upsert_price_bars(symbol, timeframe, frame)
                merged[symbol] = merge_frames(merged.get(symbol), frame)
            print(
                f"[fetch] timeframe={timeframe} chunk={chunk_index}/{len(chunks)} "
                f"window={window_index}/{len(windows)} symbols={len(symbol_chunk)} "
                f"start={window_start.date().isoformat()} end={window_end.date().isoformat()}",
                flush=True,
            )

    return {symbol: ensure_utc_naive(merged.get(symbol)) for symbol in symbols}


def load_symbol_histories(
    settings: AppConfig,
    market_data: AlpacaClient,
    storage: SQLiteStorage,
    symbols: list[str],
    benchmark_symbol: str,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, SymbolHistory], SymbolHistory]:
    market_timezone = ZoneInfo(settings.app.timezone)
    week_start = datetime.combine(start_date - timedelta(days=WEEKLY_BUFFER_DAYS), clock_time.min, tzinfo=UTC)
    day_start = datetime.combine(start_date - timedelta(days=DAILY_BUFFER_DAYS), clock_time.min, tzinfo=UTC)
    hour_start = datetime.combine(start_date - timedelta(days=HOURLY_BUFFER_DAYS), clock_time.min, tzinfo=UTC)
    end_dt = datetime.combine(end_date + timedelta(days=1), clock_time.min, tzinfo=UTC)

    weekly_frames = fetch_timeframe_history(market_data, storage, symbols + [benchmark_symbol], "week", week_start, end_dt)
    daily_frames = fetch_timeframe_history(market_data, storage, symbols + [benchmark_symbol], "day", day_start, end_dt)
    hourly_frames = fetch_timeframe_history(market_data, storage, symbols, "hour", hour_start, end_dt)

    histories: dict[str, SymbolHistory] = {}
    for symbol in symbols:
        histories[symbol] = SymbolHistory(
            symbol=symbol,
            weekly=to_bundle(weekly_frames.get(symbol, pd.DataFrame()), market_timezone),
            daily=to_bundle(daily_frames.get(symbol, pd.DataFrame()), market_timezone),
            hourly=to_bundle(hourly_frames.get(symbol, pd.DataFrame()), market_timezone),
        )

    benchmark_history = SymbolHistory(
        symbol=benchmark_symbol,
        weekly=to_bundle(weekly_frames.get(benchmark_symbol, pd.DataFrame()), market_timezone),
        daily=to_bundle(daily_frames.get(benchmark_symbol, pd.DataFrame()), market_timezone),
        hourly=to_bundle(pd.DataFrame(), market_timezone),
    )
    return histories, benchmark_history


def derive_sessions(benchmark_history: SymbolHistory, start_date: date, end_date: date, market_timezone: str) -> list[date]:
    daily_frame = benchmark_history.daily.frame
    if daily_frame.empty:
        return []
    local_index = pd.DatetimeIndex(daily_frame.index).tz_localize("UTC").tz_convert(market_timezone)
    sessions = sorted({item.date() for item in local_index if start_date <= item.date() <= end_date})
    return sessions


def classify_candidate(
    history: SymbolHistory,
    market_trend: str,
    settings: AppConfig,
    session_date: date,
) -> CandidateRecord | None:
    weekly_frame = slice_to_session(history.weekly, session_date)
    if weekly_frame.empty:
        return None

    weekly = indicators.screen_weekly(weekly_frame, settings.strategy)
    if not weekly.get("actionable") or not weekly.get("pass"):
        return None

    direction = weekly.get("trend")
    if direction not in {"LONG", "SHORT"}:
        return None
    if settings.market_filter.enabled and direction == "LONG" and market_trend == "SHORT":
        return None

    daily_frame = slice_to_session(history.daily, session_date)
    if daily_frame.empty:
        return None
    daily = indicators.screen_daily(daily_frame, direction, settings.strategy)
    if not daily.get("pass"):
        return None

    return CandidateRecord(
        symbol=history.symbol,
        direction=direction,
        source_session_date=session_date.isoformat(),
    )


def refresh_candidate(
    history: SymbolHistory,
    candidate: CandidateRecord,
    market_trend: str,
    settings: AppConfig,
    session_date: date,
) -> CandidateRecord | None:
    weekly_frame = slice_to_session(history.weekly, session_date)
    if weekly_frame.empty:
        return None
    weekly = indicators.screen_weekly(weekly_frame, settings.strategy)
    if not weekly.get("actionable") or not weekly.get("pass") or weekly.get("trend") != candidate.direction:
        return None
    if settings.market_filter.enabled and candidate.direction == "LONG" and market_trend == "SHORT":
        return None

    daily_frame = slice_to_session(history.daily, session_date)
    if daily_frame.empty:
        return None
    daily = indicators.screen_daily(daily_frame, candidate.direction, settings.strategy)
    if daily.get("state") == "REJECT":
        return None
    return candidate


def compute_market_trend(benchmark_history: SymbolHistory, settings: AppConfig, session_date: date) -> str:
    if not settings.market_filter.enabled:
        return "UNKNOWN"
    weekly_frame = slice_to_session(benchmark_history.weekly, session_date)
    if weekly_frame.empty:
        return "UNKNOWN"
    weekly = indicators.screen_weekly(weekly_frame, settings.strategy)
    return str(weekly.get("trend") or "UNKNOWN")


def build_watchlist(
    qualified_sessions: dict[str, list[CandidateRecord]],
    histories: dict[str, SymbolHistory],
    settings: AppConfig,
    market_trend: str,
    prior_sessions: list[date],
) -> list[CandidateRecord]:
    deduped: dict[tuple[str, str], CandidateRecord] = {}
    for session_date in reversed(prior_sessions[-WATCHLIST_SESSION_LIMIT:]):
        for candidate in qualified_sessions.get(session_date.isoformat(), []):
            key = (candidate.symbol, candidate.direction)
            if key in deduped:
                continue
            refreshed = refresh_candidate(histories[candidate.symbol], candidate, market_trend, settings, prior_sessions[-1])
            if refreshed:
                deduped[key] = refreshed
    return list(deduped.values())


def build_as_of(timestamp: pd.Timestamp) -> datetime:
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return (timestamp + pd.Timedelta(minutes=30)).to_pydatetime()


def compute_position_open_risk(position: Position) -> float:
    return float(
        compute_used_stop(
            entry_price=position.entry_price,
            stop_loss=position.active_stop,
            shares=position.shares,
            direction=position.direction,
        )
        or 0.0
    )


def compute_total_open_risk(open_positions: dict[str, Position]) -> float:
    return sum(compute_position_open_risk(position) for position in open_positions.values())


def compute_position_equity_component(position: Position) -> float:
    unrealized_pnl_per_share = (
        position.last_price - position.entry_price
        if position.direction == "LONG"
        else position.entry_price - position.last_price
    )
    return round(position.position_cost + unrealized_pnl_per_share * position.shares, 4)


def compute_account_equity(cash: float, open_positions: dict[str, Position], margin_debt: float = 0.0) -> float:
    return round(cash + sum(compute_position_equity_component(position) for position in open_positions.values()) - margin_debt, 4)


def compute_remaining_stop_budget(
    account_equity: float,
    max_total_open_risk_pct: float,
    open_positions: dict[str, Position],
) -> float:
    if max_total_open_risk_pct <= 0:
        return math.inf
    total_budget = max(account_equity, 0.0) * (max_total_open_risk_pct / 100.0)
    return max(0.0, total_budget - compute_total_open_risk(open_positions))


def compute_position_size(
    account_equity: float,
    cash_available: float,
    risk_pct: float,
    remaining_stop_budget: float,
    risk_per_share: float,
    entry_price: float,
) -> PositionSizing:
    if account_equity <= 0 or cash_available <= 0 or risk_pct <= 0 or risk_per_share <= 0 or entry_price <= 0:
        return PositionSizing(shares=0, used_risk=0.0, position_cost=0.0, allowed_risk=0.0)

    allowed_risk = account_equity * (risk_pct / 100.0)
    if math.isfinite(remaining_stop_budget):
        allowed_risk = min(allowed_risk, remaining_stop_budget)
    if allowed_risk <= 0:
        return PositionSizing(shares=0, used_risk=0.0, position_cost=0.0, allowed_risk=0.0)

    shares_by_risk = math.floor(allowed_risk / risk_per_share)
    shares_by_cash = math.floor(cash_available / entry_price)
    shares = int(max(0, min(shares_by_risk, shares_by_cash)))
    position_cost = round(shares * entry_price, 4)
    used_risk = round(shares * risk_per_share, 4)
    return PositionSizing(
        shares=shares,
        used_risk=used_risk,
        position_cost=position_cost,
        allowed_risk=round(allowed_risk, 4),
    )


def is_stop_hit(direction: str, active_stop: float, bar: pd.Series) -> bool:
    return (direction == "LONG" and float(bar["low"]) <= active_stop) or (
        direction == "SHORT" and float(bar["high"]) >= active_stop
    )


def exit_price_from_stop(active_stop: float) -> float:
    return round(active_stop, 4)


def r_multiple(direction: str, entry_price: float, exit_price: float, risk_per_share: float) -> float:
    if risk_per_share <= 0:
        return 0.0
    pnl_per_share = exit_price - entry_price if direction == "LONG" else entry_price - exit_price
    return round(pnl_per_share / risk_per_share, 4)


def mark_exit(
    position: Position,
    exit_timestamp: pd.Timestamp,
    exit_price: float,
    exit_reason: str,
) -> TradeResult:
    pnl_per_share = exit_price - position.entry_price if position.direction == "LONG" else position.entry_price - exit_price
    pnl = pnl_per_share * position.shares
    pct = (pnl_per_share / position.entry_price) * 100 if position.entry_price else 0.0
    return TradeResult(
        symbol=position.symbol,
        direction=position.direction,
        source_session_date=position.source_session_date,
        entry_session_date=position.entry_session_date,
        entry_timestamp=position.entry_timestamp,
        exit_session_date=exit_timestamp.date().isoformat(),
        exit_timestamp=exit_timestamp.isoformat(),
        entry_price=round(position.entry_price, 4),
        exit_price=round(exit_price, 4),
        initial_stop=round(position.initial_stop, 4),
        final_stop=round(position.active_stop, 4),
        shares=position.shares,
        pnl=round(pnl, 4),
        pnl_pct=round(pct, 4),
        r_multiple=r_multiple(position.direction, position.entry_price, exit_price, position.risk_per_share),
        bars_held=position.bars_held,
        exit_reason=exit_reason,
        position_cost=round(position.position_cost, 4),
        entry_cash_before=round(position.entry_cash_before, 4),
        entry_equity_before=round(position.entry_equity_before, 4),
        entry_open_risk_before=round(position.entry_open_risk_before, 4),
        entry_remaining_stop_budget=round(position.entry_remaining_stop_budget, 4),
        entry_allowed_risk=round(position.entry_allowed_risk, 4),
    )


def update_equity_stats(
    cash: float,
    open_positions: dict[str, Position],
    margin_debt: float,
    max_equity: float,
    max_drawdown_pct: float,
) -> tuple[float, float]:
    equity = compute_account_equity(cash, open_positions, margin_debt=margin_debt)
    next_max_equity = max(max_equity, equity)
    drawdown = 0.0 if next_max_equity <= 0 else (next_max_equity - equity) / next_max_equity * 100
    return next_max_equity, max(max_drawdown_pct, drawdown)


def collect_session_timestamps(session_frames: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    timestamps: set[pd.Timestamp] = set()
    for frame in session_frames.values():
        if frame is None or frame.empty:
            continue
        timestamps.update(pd.Timestamp(item) for item in frame.index)
    return sorted(timestamps)


def run_backtest(
    settings: AppConfig,
    start_date: date,
    end_date: date,
    risk_pct: float,
    max_total_open_risk_pct: float,
    max_open_positions: int,
    initial_capital: float,
    initial_buying_power: float,
    max_symbols: int,
) -> dict[str, Any]:
    if initial_buying_power > 0 and initial_buying_power < initial_capital:
        raise ValueError("Initial buying power cannot be lower than initial capital.")

    storage = SQLiteStorage(settings.storage.database_path)
    storage.init_db()

    market_data = AlpacaClient(settings.alpaca, storage=storage, market_timezone=settings.app.timezone)
    universe_rows = market_data.get_top_symbols(settings.universe)
    symbols = [row["symbol"] for row in universe_rows if row.get("symbol")]
    if max_symbols > 0:
        symbols = symbols[:max_symbols]

    benchmark_symbol = settings.market_filter.benchmark_symbol
    histories, benchmark_history = load_symbol_histories(settings, market_data, storage, symbols, benchmark_symbol, start_date, end_date)
    sessions = derive_sessions(benchmark_history, start_date, end_date, settings.app.timezone)
    if len(sessions) < 2:
        raise RuntimeError("Not enough benchmark sessions to run the backtest.")

    qualified_sessions: dict[str, list[CandidateRecord]] = {}
    open_positions: dict[str, Position] = {}
    closed_trades: list[TradeResult] = []
    buying_power = initial_buying_power if initial_buying_power > 0 else initial_capital
    margin_debt = max(0.0, buying_power - initial_capital)
    cash = buying_power
    max_equity = initial_capital
    max_drawdown_pct = 0.0
    triggered_candidates = 0
    qualified_candidates = 0
    skipped_rr = 0
    skipped_open_risk_cap = 0
    skipped_cash_cap = 0
    skipped_position_cap = 0

    for session_index, session_date in enumerate(sessions):
        if session_index % 25 == 0:
            print(
                f"[replay] session={session_index + 1}/{len(sessions)} date={session_date.isoformat()} "
                f"open_positions={len(open_positions)} closed_trades={len(closed_trades)}",
                flush=True,
            )

        if session_index > 0:
            prior_session = sessions[session_index - 1]
            market_trend = compute_market_trend(benchmark_history, settings, prior_session)
            watchlist = build_watchlist(
                qualified_sessions=qualified_sessions,
                histories=histories,
                settings=settings,
                market_trend=market_trend,
                prior_sessions=sessions[:session_index],
            )
            blocked_today: set[str] = set()
            relevant_symbols = sorted(set(open_positions) | {candidate.symbol for candidate in watchlist})
            session_frames = {
                symbol: slice_session_hours(histories[symbol].hourly, session_date) for symbol in relevant_symbols if symbol in histories
            }

            for timestamp in collect_session_timestamps(session_frames):
                current_timestamp = pd.Timestamp(timestamp).tz_localize("UTC")

                for symbol, position in list(open_positions.items()):
                    frame = session_frames.get(symbol)
                    if frame is None or frame.empty or timestamp not in frame.index:
                        continue

                    current_bar = frame.loc[timestamp]
                    if isinstance(current_bar, pd.DataFrame):
                        current_bar = current_bar.iloc[-1]

                    position.last_price = float(current_bar["close"])
                    position.bars_held += 1

                    if not is_stop_hit(position.direction, position.active_stop, current_bar):
                        open_positions[symbol] = position
                        continue

                    trade = mark_exit(
                        position=position,
                        exit_timestamp=current_timestamp,
                        exit_price=exit_price_from_stop(position.active_stop),
                        exit_reason="STOP",
                    )
                    cash += position.position_cost + trade.pnl
                    closed_trades.append(trade)
                    blocked_today.add(symbol)
                    del open_positions[symbol]

                triggers: list[TriggerEvent] = []
                for candidate in watchlist:
                    if candidate.symbol in open_positions or candidate.symbol in blocked_today:
                        continue

                    history = histories[candidate.symbol]
                    daily_frame = slice_to_session(history.daily, prior_session)
                    if daily_frame.empty:
                        continue

                    session_hours = session_frames.get(candidate.symbol)
                    if session_hours is None or session_hours.empty or timestamp not in session_hours.index:
                        continue

                    current_bar = session_hours.loc[timestamp]
                    if isinstance(current_bar, pd.DataFrame):
                        current_bar = current_bar.iloc[-1]

                    full_hourly_frame = history.hourly.frame.loc[:timestamp]
                    hourly = indicators.screen_hourly(
                        full_hourly_frame,
                        candidate.direction,
                        settings.strategy,
                        as_of=build_as_of(current_timestamp),
                    )
                    if not hourly.get("pass"):
                        continue

                    exits = indicators.calc_exits(
                        candidate.direction,
                        float(hourly["entry_price"]),
                        daily_frame,
                        float(hourly["atr"]),
                        settings.trade_plan,
                        signal_bar_high=hourly.get("signal_bar_high"),
                        signal_bar_low=hourly.get("signal_bar_low"),
                    )
                    if (
                        float(exits.get("reward_risk_ratio_model", 0.0) or 0.0)
                        < settings.qualification.intraday_minimum_reward_risk
                    ):
                        skipped_rr += 1
                        continue

                    triggers.append(
                        TriggerEvent(
                            candidate=candidate,
                            timestamp=current_timestamp,
                            current_bar=current_bar,
                            exits=exits,
                        )
                    )

                for trigger in triggers:
                    if max_open_positions > 0 and len(open_positions) >= max_open_positions:
                        skipped_position_cap += 1
                        continue

                    account_equity = compute_account_equity(cash, open_positions, margin_debt=margin_debt)
                    open_risk_before = compute_total_open_risk(open_positions)
                    remaining_stop_budget = compute_remaining_stop_budget(
                        account_equity=account_equity,
                        max_total_open_risk_pct=max_total_open_risk_pct,
                        open_positions=open_positions,
                    )
                    if max_total_open_risk_pct > 0 and remaining_stop_budget <= 0:
                        skipped_open_risk_cap += 1
                        continue

                    risk_per_share = float(trigger.exits.get("risk_per_share_model", 0.0) or 0.0)
                    sizing = compute_position_size(
                        account_equity=account_equity,
                        cash_available=cash,
                        risk_pct=risk_pct,
                        remaining_stop_budget=remaining_stop_budget,
                        risk_per_share=risk_per_share,
                        entry_price=float(trigger.exits["entry"]),
                    )
                    if sizing.shares <= 0:
                        entry_cost = float(trigger.exits["entry"])
                        if entry_cost > cash or sizing.position_cost <= 0:
                            skipped_cash_cap += 1
                        else:
                            skipped_open_risk_cap += 1
                        continue

                    position = Position(
                        symbol=trigger.candidate.symbol,
                        direction=trigger.candidate.direction,
                        entry_session_date=session_date.isoformat(),
                        entry_timestamp=trigger.timestamp.isoformat(),
                        entry_price=float(trigger.exits["entry"]),
                        initial_stop=float(trigger.exits["initial_stop_model_loss"]),
                        active_stop=float(trigger.exits["initial_stop_model_loss"]),
                        risk_per_share=risk_per_share,
                        shares=sizing.shares,
                        take_profit=float(trigger.exits["take_profit"]),
                        source_session_date=trigger.candidate.source_session_date,
                        bars_held=1,
                        position_cost=sizing.position_cost,
                        last_price=float(trigger.current_bar["close"]),
                        entry_cash_before=round(cash, 4),
                        entry_equity_before=round(account_equity, 4),
                        entry_open_risk_before=round(open_risk_before, 4),
                        entry_remaining_stop_budget=round(remaining_stop_budget, 4),
                        entry_allowed_risk=round(sizing.allowed_risk, 4),
                    )

                    cash -= sizing.position_cost
                    triggered_candidates += 1
                    blocked_today.add(trigger.candidate.symbol)

                    if is_stop_hit(position.direction, position.active_stop, trigger.current_bar):
                        trade = mark_exit(
                            position=position,
                            exit_timestamp=trigger.timestamp,
                            exit_price=exit_price_from_stop(position.active_stop),
                            exit_reason="SAME_BAR_STOP",
                        )
                        cash += position.position_cost + trade.pnl
                        closed_trades.append(trade)
                    else:
                        open_positions[trigger.candidate.symbol] = position

                max_equity, max_drawdown_pct = update_equity_stats(
                    cash,
                    open_positions,
                    margin_debt,
                    max_equity,
                    max_drawdown_pct,
                )

            for symbol, position in list(open_positions.items()):
                daily_frame = slice_to_session(histories[symbol].daily, session_date)
                if daily_frame.empty:
                    continue
                atr_stops, _ = indicators.calc_atr_stops(daily_frame, position.direction, atr_period=14)
                proposed_stop = atr_stops.get(1.0)
                latest_close = float(daily_frame["close"].iloc[-1])
                open_profit = compute_open_profit(position.entry_price, latest_close, position.shares, position.direction)
                locked_profit = compute_stop_locked_profit(position.entry_price, proposed_stop, position.shares, position.direction)
                capture_pct = compute_profit_capture_pct(open_profit, locked_profit)
                warning_triggered = should_block_stop_relaxation(
                    position.active_stop,
                    proposed_stop,
                    position.direction,
                    open_profit,
                    capture_pct,
                )
                if proposed_stop is not None and not warning_triggered:
                    position.active_stop = float(proposed_stop)

                session_frame = session_frames.get(symbol)
                if session_frame is not None and not session_frame.empty:
                    position.last_price = float(session_frame["close"].iloc[-1])
                open_positions[symbol] = position

            max_equity, max_drawdown_pct = update_equity_stats(
                cash,
                open_positions,
                margin_debt,
                max_equity,
                max_drawdown_pct,
            )

        market_trend = compute_market_trend(benchmark_history, settings, session_date)
        today_candidates: list[CandidateRecord] = []
        for symbol in symbols:
            candidate = classify_candidate(histories[symbol], market_trend, settings, session_date)
            if candidate:
                today_candidates.append(candidate)
        qualified_sessions[session_date.isoformat()] = today_candidates
        qualified_candidates += len(today_candidates)

    final_session = sessions[-1]
    for symbol, position in list(open_positions.items()):
        daily_frame = slice_to_session(histories[symbol].daily, final_session)
        if daily_frame.empty:
            continue
        exit_price = float(daily_frame["close"].iloc[-1])
        exit_timestamp = pd.Timestamp(daily_frame.index[-1]).tz_localize("UTC")
        trade = mark_exit(
            position=position,
            exit_timestamp=exit_timestamp,
            exit_price=exit_price,
            exit_reason="MARK_TO_MARKET",
        )
        cash += position.position_cost + trade.pnl
        closed_trades.append(trade)
        del open_positions[symbol]

    max_equity, max_drawdown_pct = update_equity_stats(
        cash,
        open_positions,
        margin_debt,
        max_equity,
        max_drawdown_pct,
    )

    closed_trades.sort(key=lambda item: item.entry_timestamp)
    wins = [trade for trade in closed_trades if trade.pnl > 0]
    losses = [trade for trade in closed_trades if trade.pnl < 0]
    total_pnl = sum(trade.pnl for trade in closed_trades)
    total_r = sum(trade.r_multiple for trade in closed_trades)
    ending_equity = compute_account_equity(cash, open_positions, margin_debt=margin_debt)
    total_return_pct = ((ending_equity - initial_capital) / initial_capital * 100) if initial_capital else 0.0
    avg_trade_pct = (sum(trade.pnl_pct for trade in closed_trades) / len(closed_trades)) if closed_trades else 0.0
    avg_r = (total_r / len(closed_trades)) if closed_trades else 0.0

    assumptions = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "initial_capital": round(initial_capital, 2),
        "initial_buying_power": round(buying_power, 2),
        "initial_margin_debt": round(margin_debt, 2),
        "risk_pct_per_trade": round(risk_pct, 4),
        "max_total_open_risk_pct": round(max_total_open_risk_pct, 4),
        "max_open_positions": max_open_positions,
        "universe_size": len(symbols),
        "watchlist_session_limit": WATCHLIST_SESSION_LIMIT,
        "historical_earnings_filter_included": False,
        "data_source": "sqlite-first with Alpaca backfill; fetched bars are persisted to price_bars",
        "position_sizing": "whole shares only; each entry is constrained by remaining stop budget, per-trade 2% risk logic, and available buying power",
        "exit_model": "initial stop at entry from SafeZone/Nick, daily ATR 1x stop refreshed after each close without monotonic filter, mark remaining positions to market at end",
        "same_bar_rule": "conservative; if trigger and stop both fit inside the same hour bar, exit at stop on that bar",
    }
    summary = {
        "qualified_candidates": qualified_candidates,
        "triggered_candidates": triggered_candidates,
        "rr_filtered_out": skipped_rr,
        "open_risk_cap_filtered_out": skipped_open_risk_cap,
        "cash_cap_filtered_out": skipped_cash_cap,
        "position_cap_filtered_out": skipped_position_cap,
        "trade_count": len(closed_trades),
        "win_rate_pct": round((len(wins) / len(closed_trades) * 100), 2) if closed_trades else 0.0,
        "avg_trade_return_pct": round(avg_trade_pct, 4),
        "avg_r_multiple": round(avg_r, 4),
        "total_r_multiple": round(total_r, 4),
        "total_pnl": round(total_pnl, 2),
        "ending_buying_power_cash": round(cash, 2),
        "margin_debt": round(margin_debt, 2),
        "ending_cash": round(cash, 2),
        "ending_equity": round(ending_equity, 2),
        "total_return_pct": round(total_return_pct, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "profit_factor": round(
            (sum(trade.pnl for trade in wins) / abs(sum(trade.pnl for trade in losses)))
            if losses
            else 0.0,
            4,
        ),
    }

    result = {
        "assumptions": assumptions,
        "summary": summary,
        "trades": [asdict(trade) for trade in closed_trades],
    }

    run_id = storage.insert_backtest_run(
        {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "initial_capital": initial_capital,
            "risk_pct": risk_pct,
            "max_total_open_risk_pct": max_total_open_risk_pct,
            "max_open_positions": max_open_positions,
            "assumptions": assumptions,
            "summary": summary,
        },
        result["trades"],
    )
    result["run_id"] = run_id
    return result


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings()
    start_date, end_date = resolve_period(args)
    result = run_backtest(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        risk_pct=args.risk_pct,
        max_total_open_risk_pct=args.max_total_open_risk_pct,
        max_open_positions=args.max_open_positions,
        initial_capital=args.initial_capital,
        initial_buying_power=args.initial_buying_power,
        max_symbols=args.max_symbols,
    )

    print(json.dumps({"run_id": result["run_id"], **result["summary"]}, ensure_ascii=False, indent=2))
    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nDetailed results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
