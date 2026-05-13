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

    return max(0, round(risk_per_share * shares, 4))


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


def choose_monotonic_stop_anchor(
    current_stop: float | None,
    previous_suggested_stop: float | None,
    direction: str | None,
) -> float | None:
    if current_stop is None:
        return previous_suggested_stop
    return current_stop


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
            current_stop = _to_float(trade.get("stop_loss"))
            previous_suggested_stop = _to_float(trade.get("suggested_stop_loss"))
            monotonic_anchor = choose_monotonic_stop_anchor(current_stop, previous_suggested_stop, direction)

            if not trade_id or not symbol:
                updates.append(
                    {
                        "trade_id": trade_id,
                        "symbol": symbol or "UNKNOWN",
                        "direction": to_storage_direction(direction),
                        "session_date": target_session,
                        "status": "ERROR",
                        "changed": False,
                        "note": "Missing trade ID or ticker; cannot update stop.",
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
                        "note": "Missing entry price or shares; cannot calculate protective stop.",
                    }
                )
                continue

            try:
                daily_frame = self.market_data.get_daily_bars(symbol)
                if daily_frame is None or daily_frame.empty:
                    raise ValueError("Insufficient daily data")

                atr_stops, _ = indicators.calc_atr_stops(daily_frame, direction, atr_period=14)
                proposed_stop = _to_float(atr_stops.get(1.0))
                proposed_stop_wide = _to_float(atr_stops.get(2.0))

                hourly_frame = getattr(self.market_data, "get_hourly_bars", lambda _: None)(symbol)
                hourly_safezone_stop, _ = (
                    indicators.calc_safezone_stop(hourly_frame, direction, self.trade_plan)
                    if hourly_frame is not None and not hourly_frame.empty
                    else (None, 0.0)
                )
                proposed_stop_hourly_safezone = _round_or_none(hourly_safezone_stop)
                applied_stop = apply_monotonic_stop(monotonic_anchor, proposed_stop, direction)
                used_stop = compute_used_stop(entry_price, applied_stop, shares, direction)
                changed = monotonic_anchor is None or (
                    applied_stop is not None and abs(float(applied_stop) - float(monotonic_anchor)) > 1e-9
                )
                status = "UPDATED" if changed else "UNCHANGED"
                note = "Suggested stop updated using the one-way rule" if changed else "Suggested stop unchanged"
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
                        "current_stop_loss": _round_or_none(current_stop),
                        "previous_stop_loss": _round_or_none(monotonic_anchor),
                        "proposed_stop_loss": _round_or_none(proposed_stop),
                        "proposed_stop_loss_atr_2x": _round_or_none(proposed_stop_wide),
                        "proposed_stop_hourly_safezone": proposed_stop_hourly_safezone,
                        "applied_stop_loss": _round_or_none(applied_stop),
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
                        "current_stop_loss": _round_or_none(current_stop),
                        "previous_stop_loss": _round_or_none(monotonic_anchor),
                        "status": "ERROR",
                        "changed": False,
                        "note": str(exc),
                    }
                )

        if apply_changes:
            self.storage.insert_trade_stop_updates(updates)

        updated_count = sum(1 for item in updates if item.get("status") == "UPDATED")
        unchanged_count = sum(1 for item in updates if item.get("status") == "UNCHANGED")
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
