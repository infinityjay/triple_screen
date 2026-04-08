from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import indicators
from alpaca import AlpacaClient
from schema import TradePlanConfig
from sqlite import SQLiteStorage


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
        target_session = (session_date or date.today()).isoformat()
        trades = self.storage.list_open_trades()
        updates: list[dict[str, Any]] = []

        for trade in trades:
            trade_id = str(trade.get("id", ""))
            symbol = str(trade.get("stock", "")).strip().upper()
            direction = normalize_trade_direction(str(trade.get("direction", "long")))
            entry_price = _to_float(trade.get("buy_price"))
            shares = _to_float(trade.get("shares"))
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

                exits = indicators.calc_exits(
                    direction,
                    entry_price,
                    daily_frame,
                    atr=0.0,
                    trade_plan=self.trade_plan,
                )
                proposed_stop = _to_float(exits.get("stop_loss"))
                applied_stop = apply_monotonic_stop(previous_stop, proposed_stop, direction)
                used_stop = compute_used_stop(entry_price, applied_stop, shares, direction)
                changed = previous_stop is None or (
                    applied_stop is not None and abs(float(applied_stop) - float(previous_stop)) > 1e-9
                )
                status = "UPDATED" if changed else "UNCHANGED"
                note = "保护性止损已上移" if changed and direction == LONG else "保护性止损已下移" if changed else "保护性止损维持不变"

                if apply_changes:
                    self.storage.update_trade_protective_stop(
                        trade_id=trade_id,
                        stop_loss=applied_stop,
                        used_stop=used_stop,
                        stop_basis=str(exits.get("stop_basis", "UNKNOWN")),
                        session_date=target_session,
                    )
                updates.append(
                    {
                        "trade_id": trade_id,
                        "symbol": symbol,
                        "direction": to_storage_direction(direction),
                        "session_date": target_session,
                        "previous_stop_loss": _round_or_none(previous_stop),
                        "proposed_stop_loss": _round_or_none(proposed_stop),
                        "applied_stop_loss": _round_or_none(applied_stop),
                        "stop_basis": str(exits.get("stop_basis", "UNKNOWN")),
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
