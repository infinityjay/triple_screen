from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import indicators
from clients.alpaca import AlpacaClient
from config.schema import TradePlanConfig
from storage.sqlite import SQLiteStorage


LONG = "LONG"
SHORT = "SHORT"


def normalize_trade_direction(value: str | None) -> str:
    return SHORT if str(value or "").strip().lower() == "short" else LONG


def to_storage_direction(value: str | None) -> str:
    return "short" if normalize_trade_direction(value) == SHORT else "long"


def compute_used_stop(
    entry_price: float | None,
    stop_loss: float | None,
    shares: float | None,
    direction: str | None,
) -> float | None:
    if entry_price is None or stop_loss is None or shares is None:
        return None

    normalized_direction = normalize_trade_direction(direction)
    if normalized_direction == LONG:
        risk_per_share = entry_price - stop_loss
    else:
        risk_per_share = stop_loss - entry_price

    if risk_per_share <= 0:
        return 0.0
    return round(abs(risk_per_share * shares), 4)


def apply_monotonic_stop(
    current_stop: float | None,
    proposed_stop: float | None,
    direction: str | None,
) -> float | None:
    if proposed_stop is None:
        return current_stop
    if current_stop is None:
        return proposed_stop
    normalized_direction = normalize_trade_direction(direction)
    if normalized_direction == LONG:
        return max(current_stop, proposed_stop)
    return min(current_stop, proposed_stop)


def is_stop_relaxation(
    current_stop: float | None,
    proposed_stop: float | None,
    direction: str | None,
) -> bool:
    if current_stop is None or proposed_stop is None:
        return False
    normalized_direction = normalize_trade_direction(direction)
    if normalized_direction == LONG:
        return proposed_stop < current_stop
    return proposed_stop > current_stop


def compute_open_profit(
    entry_price: float | None,
    current_price: float | None,
    shares: float | None,
    direction: str | None,
) -> float | None:
    if entry_price is None or current_price is None or shares is None:
        return None

    normalized_direction = normalize_trade_direction(direction)
    pnl_per_share = current_price - entry_price if normalized_direction == LONG else entry_price - current_price
    return round(pnl_per_share * shares, 4)


def compute_stop_locked_profit(
    entry_price: float | None,
    stop_price: float | None,
    shares: float | None,
    direction: str | None,
) -> float | None:
    if entry_price is None or stop_price is None or shares is None:
        return None

    normalized_direction = normalize_trade_direction(direction)
    pnl_per_share = stop_price - entry_price if normalized_direction == LONG else entry_price - stop_price
    return round(pnl_per_share * shares, 4)


def compute_profit_capture_pct(open_profit: float | None, locked_profit: float | None) -> float | None:
    if open_profit is None or locked_profit is None or open_profit <= 0:
        return None
    return round((locked_profit / open_profit) * 100, 2)


def should_block_stop_relaxation(
    current_stop: float | None,
    proposed_stop: float | None,
    direction: str | None,
    open_profit: float | None,
    capture_pct: float | None,
    min_capture_pct: float = 33.33,
) -> bool:
    if open_profit is None or open_profit <= 0 or capture_pct is None:
        return False
    if not is_stop_relaxation(current_stop, proposed_stop, direction):
        return False
    return capture_pct < min_capture_pct


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


@dataclass(frozen=True)
class StopUpdateSummary:
    session_date: str
    total_positions: int
    updated_count: int
    unchanged_count: int
    error_count: int
    updates: list[dict[str, Any]]


class JournalManager:
    def __init__(
        self,
        storage: SQLiteStorage,
        market_data: AlpacaClient,
        trade_plan: TradePlanConfig,
    ) -> None:
        self.storage = storage
        self.market_data = market_data
        self.trade_plan = trade_plan

    def update_open_position_stops(self, session_date: date | None = None) -> StopUpdateSummary:
        return self._update_open_position_stops(session_date=session_date, apply_changes=True)

    def _update_open_position_stops(
        self,
        session_date: date | None = None,
        apply_changes: bool = True,
    ) -> StopUpdateSummary:
        target_session = (session_date or datetime.now(UTC).date()).isoformat()
        trades = self.storage.list_open_trades()
        updates: list[dict[str, Any]] = []

        for trade in trades:
            trade_id = str(trade.get("id", ""))
            symbol = str(trade.get("stock", "")).strip().upper()
            direction = normalize_trade_direction(str(trade.get("direction", "long")))
            entry_price = _to_float(trade.get("buy_price"))
            shares = _to_float(trade.get("shares"))
            previous_stop = _to_float(trade.get("suggested_stop_loss"))
            if previous_stop is None:
                previous_stop = _to_float(trade.get("stop_loss"))

            if not trade_id or not symbol:
                updates.append(
                    {
                        "trade_id": trade_id,
                        "symbol": symbol or "UNKNOWN",
                        "direction": to_storage_direction(direction),
                        "session_date": target_session,
                        "status": "ERROR",
                        "changed": False,
                        "note": "缺少交易 ID 或股票代码，无法更新止损。",
                    }
                )
                continue

            if entry_price is None or shares is None:
                updates.append(
                    {
                        "trade_id": trade_id,
                        "symbol": symbol,
                        "direction": to_storage_direction(direction),
                        "session_date": target_session,
                        "status": "ERROR",
                        "changed": False,
                        "note": "缺少入场价或股数，无法计算保护性止损。",
                    }
                )
                continue

            try:
                daily_frame = self.market_data.get_daily_bars(symbol)
                if daily_frame is None or daily_frame.empty:
                    raise ValueError("日线数据不足")

                atr_stops, _ = indicators.calc_atr_stops(daily_frame, direction, atr_period=14)
                latest_close = _to_float(daily_frame["close"].iloc[-1])
                proposed_stop = _to_float(atr_stops.get(1.0))
                proposed_stop_wide = _to_float(atr_stops.get(2.0))
                applied_stop = proposed_stop
                used_stop = compute_used_stop(entry_price, applied_stop, shares, direction)
                open_profit = compute_open_profit(entry_price, latest_close, shares, direction)
                locked_profit = compute_stop_locked_profit(entry_price, proposed_stop, shares, direction)
                locked_profit_wide = compute_stop_locked_profit(entry_price, proposed_stop_wide, shares, direction)
                capture_pct = compute_profit_capture_pct(open_profit, locked_profit)
                capture_pct_wide = compute_profit_capture_pct(open_profit, locked_profit_wide)
                warning_triggered = should_block_stop_relaxation(
                    previous_stop,
                    proposed_stop,
                    direction,
                    open_profit,
                    capture_pct,
                )
                if warning_triggered:
                    applied_stop = previous_stop
                    changed = False
                    status = "WARNING"
                    note = (
                        f"新止损若继续放松，只能锁住 {capture_pct:.2f}% 当前浮盈；"
                        "会回吐超过 2/3 浮盈，停止继续下调。"
                    )
                else:
                    changed = previous_stop is None or (
                        proposed_stop is not None and abs(float(proposed_stop) - float(previous_stop)) > 1e-9
                    )
                    status = "UPDATED" if changed else "UNCHANGED"
                    note = "ATR 移动止损建议已重新计算" if changed else "ATR 移动止损建议维持不变"
                stop_basis = "ATR_1X" if proposed_stop is not None else "UNKNOWN"

                if apply_changes:
                    self.storage.update_trade_protective_stop(
                        trade_id=trade_id,
                        stop_loss=applied_stop,
                        used_stop=used_stop,
                        stop_basis=stop_basis,
                        session_date=target_session,
                    )
                updates.append(
                    {
                        "trade_id": trade_id,
                        "symbol": symbol,
                        "direction": to_storage_direction(direction),
                        "session_date": target_session,
                        "previous_stop_loss": _round_or_none(previous_stop),
                        "latest_close": _round_or_none(latest_close),
                        "open_profit": _round_or_none(open_profit),
                        "proposed_stop_loss": _round_or_none(proposed_stop),
                        "proposed_stop_loss_atr_2x": _round_or_none(proposed_stop_wide),
                        "applied_stop_loss": _round_or_none(applied_stop),
                        "locked_profit_atr_1x": _round_or_none(locked_profit),
                        "locked_profit_atr_2x": _round_or_none(locked_profit_wide),
                        "profit_capture_pct_atr_1x": _round_or_none(capture_pct, 2),
                        "profit_capture_pct_atr_2x": _round_or_none(capture_pct_wide, 2),
                        "warning_triggered": warning_triggered,
                        "stop_basis": stop_basis,
                        "changed": changed,
                        "status": status,
                        "note": note,
                    }
                )
            except Exception as exc:
                updates.append(
                    {
                        "trade_id": trade_id,
                        "symbol": symbol,
                        "direction": to_storage_direction(direction),
                        "session_date": target_session,
                        "previous_stop_loss": _round_or_none(previous_stop),
                        "status": "ERROR",
                        "changed": False,
                        "note": str(exc),
                    }
                )

        if apply_changes:
            self.storage.insert_trade_stop_updates(updates)

        updated_count = sum(1 for item in updates if item.get("status") == "UPDATED")
        unchanged_count = sum(1 for item in updates if item.get("status") in {"UNCHANGED", "WARNING"})
        error_count = sum(1 for item in updates if item.get("status") == "ERROR")

        return StopUpdateSummary(
            session_date=target_session,
            total_positions=len(trades),
            updated_count=updated_count,
            unchanged_count=unchanged_count,
            error_count=error_count,
            updates=updates,
        )

    def preview_open_position_stops(self, session_date: date | None = None) -> StopUpdateSummary:
        return self._update_open_position_stops(session_date=session_date, apply_changes=False)
