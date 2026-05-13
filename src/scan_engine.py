from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

import indicators
import trading_models
from clients.alpaca import AlpacaClient
from clients.earnings import EarningsCalendarClient
from clients.telegram import TelegramNotifier
from config.schema import AppConfig
from journal import JournalManager
from storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

TRACKING_SESSION_LIMIT = 1
HISTORY_SESSION_LIMIT = 3
STOP_LIMIT_SLIPPAGE_PCT = 0.002
STOP_LIMIT_MIN_SLIPPAGE = 0.05
PREMARKET_GAP_ALERT_PCT = 0.01


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_check_map(values: dict[str, object]) -> str:
    parts = [f"{key}={value}" for key, value in values.items()]
    return ", ".join(parts)


class TripleScreenScanner:
    def __init__(
        self,
        settings: AppConfig,
        market_data: AlpacaClient,
        earnings_calendar: EarningsCalendarClient,
        storage: SQLiteStorage,
        notifier: TelegramNotifier,
        dry_run: bool = False,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.earnings_calendar = earnings_calendar
        self.storage = storage
        self.notifier = notifier
        self.dry_run = dry_run
        self.model = trading_models.get_model(settings.trading_model.active)
        self.journal_manager = JournalManager(
            storage=storage,
            market_data=market_data,
            trade_plan=settings.trade_plan,
        )
        self.market_timezone = ZoneInfo(settings.app.timezone)
        self.market_open_time = clock_time(hour=9, minute=30)
        self.market_close_time = clock_time(hour=16, minute=0)
        self.premarket_review_start_time = clock_time(hour=9, minute=15)
        self.eod_auto_cutoff_time = clock_time(hour=16, minute=45)

    def _is_recently_alerted(self, symbol: str, direction: str) -> bool:
        row = self.storage.get_last_alert(symbol)
        if not row:
            return False
        last_alert_at = datetime.fromisoformat(row["last_alert_at"])
        if last_alert_at.tzinfo is None:
            last_alert_at = last_alert_at.replace(tzinfo=UTC)
        cooldown = timedelta(hours=self.settings.alerts.cooldown_hours)
        return _utc_now() - last_alert_at <= cooldown and row["last_direction"] == direction

    def _is_divergence_recently_alerted(self, symbol: str, direction: str) -> bool:
        row = self.storage.get_last_divergence_alert(symbol)
        if not row:
            return False
        last_alert_at = datetime.fromisoformat(row["last_alert_at"])
        if last_alert_at.tzinfo is None:
            last_alert_at = last_alert_at.replace(tzinfo=UTC)
        cooldown = timedelta(hours=self.settings.alerts.cooldown_hours)
        return _utc_now() - last_alert_at <= cooldown and row["last_direction"] == direction

    def _market_now(self) -> datetime:
        return datetime.now(self.market_timezone)

    def _previous_weekday(self, current_date: date) -> date:
        candidate = current_date - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate

    def _latest_completed_session_date(self, now_local: datetime | None = None) -> date:
        value = now_local or self._market_now()
        if value.weekday() < 5 and value.time() >= self.market_close_time:
            return value.date()
        return self._previous_weekday(value.date())

    def _is_market_open(self, now_local: datetime | None = None) -> bool:
        value = now_local or self._market_now()
        return value.weekday() < 5 and self.market_open_time <= value.time() < self.market_close_time

    def _load_universe(self) -> list[dict]:
        symbol_rows = self.market_data.get_top_symbols(self.settings.universe)
        for row in symbol_rows:
            self.storage.upsert_symbol(row["symbol"], row.get("market_cap"), row.get("sector"))
        return symbol_rows

    def _check_market_trend(self) -> str:
        if not self.settings.market_filter.enabled:
            return "UNKNOWN"

        frame = self.market_data.get_weekly_bars(self.settings.market_filter.benchmark_symbol)
        result = self.model.screen_weekly(frame, self.settings.strategy)
        return result.get("trend", "UNKNOWN")

    @staticmethod
    def _candidate_sort_key(item: dict) -> tuple[float, int, int, int]:
        status_priority = 0 if item.get("opportunity_status") == "TRIGGERED" else 1
        history_count = int(item.get("history", {}).get("appearance_count", 1) or 1)
        rank_score = float(item.get("candidate_rank_score", item.get("candidate_score", item.get("signal_score", 0.0))) or 0.0)
        return (-rank_score, -int(bool(item.get("strong_divergence"))), -history_count, status_priority)

    @staticmethod
    def _triggered_sort_key(item: dict) -> tuple[float, int]:
        return (-item.get("execution_score", item.get("signal_score", 0.0)), -int(bool(item.get("strong_divergence"))))

    @staticmethod
    def _round_price(value: float | None) -> float | None:
        if value is None:
            return None
        return round(float(value), 2)

    def _calc_stop_limit_price(self, direction: str, stop_price: float) -> float:
        slippage = max(abs(float(stop_price)) * STOP_LIMIT_SLIPPAGE_PCT, STOP_LIMIT_MIN_SLIPPAGE)
        if direction == "SHORT":
            return round(float(stop_price) - slippage, 2)
        return round(float(stop_price) + slippage, 2)

    def _build_next_day_order_plan(
        self,
        symbol: str,
        direction: str,
        session_date: date,
        daily_frame,
        weekly_frame,
        daily: dict,
        hourly_frame=None,
    ) -> dict:
        entry_plan = daily.get("entry_plan") or indicators.calc_ema_penetration_entry_plan(daily_frame, direction)
        previous_high = entry_plan.get("previous_high")
        previous_low = entry_plan.get("previous_low")
        breakout_entry = entry_plan.get("breakout_entry")
        ema_entry = entry_plan.get("ema_penetration_entry")
        action = "BUY" if direction == "LONG" else "SELL"
        side_label = "Buy" if direction == "LONG" else "Sell"
        breakout_exits = (
            self.model.calc_exits(
                direction,
                float(breakout_entry),
                daily_frame,
                0.0,
                self.settings.trade_plan,
                weekly_frame=weekly_frame,
                hourly_frame=hourly_frame,
            )
            if breakout_entry is not None
            else {}
        )
        ema_exits = (
            self.model.calc_exits(
                direction,
                float(ema_entry),
                daily_frame,
                0.0,
                self.settings.trade_plan,
                weekly_frame=weekly_frame,
                hourly_frame=hourly_frame,
            )
            if ema_entry is not None
            else {}
        )
        stop_price = self._round_price(float(breakout_entry)) if breakout_entry is not None else None
        limit_price = self._calc_stop_limit_price(direction, float(breakout_entry)) if breakout_entry is not None else None

        return {
            "available": bool(entry_plan.get("available") and breakout_entry is not None),
            "symbol": symbol,
            "direction": direction,
            "source_session_date": session_date.isoformat(),
            "intended_trade_date": self._next_weekday(session_date).isoformat(),
            "broker": "IBKR",
            "slippage_pct": STOP_LIMIT_SLIPPAGE_PCT,
            "min_slippage": STOP_LIMIT_MIN_SLIPPAGE,
            "previous_day_high": previous_high,
            "previous_day_low": previous_low,
            "breakout_entry": breakout_entry,
            "ema_penetration_entry": ema_entry,
            "primary_order": {
                "name": "Previous-day breakout Stop Limit",
                "order_type": "Stop Limit",
                "ibkr_order_type": "STP LMT",
                "action": action,
                "side_label": side_label,
                "quantity": None,
                "stop_price": stop_price,
                "limit_price": limit_price,
                "tif": "DAY",
                "outside_rth": False,
                "route": "SMART",
                "transmit": True,
                "instruction": (
                    f"{side_label} Stop Limit: Stop {stop_price:.2f}, Limit {limit_price:.2f}"
                    if stop_price is not None and limit_price is not None
                    else "Waiting for breakout price"
                ),
                "exits": breakout_exits,
            },
            "secondary_order": {
                "name": "EMA penetration limit",
                "order_type": "Limit",
                "ibkr_order_type": "LMT",
                "action": action,
                "side_label": side_label,
                "quantity": None,
                "limit_price": self._round_price(float(ema_entry)) if ema_entry is not None else None,
                "tif": "DAY",
                "outside_rth": False,
                "route": "SMART",
                "transmit": True,
                "instruction": (
                    f"{side_label} Limit: Limit {float(ema_entry):.2f}"
                    if ema_entry is not None
                    else "Waiting for EMA penetration price"
                ),
                "exits": ema_exits,
            },
            "risk": {
                "initial_stop": breakout_exits.get("initial_stop_model_loss"),
                "initial_stop_safezone": breakout_exits.get("initial_stop_safezone"),
                "initial_stop_hourly_safezone": breakout_exits.get("initial_stop_hourly_safezone"),
                "initial_stop_nick": breakout_exits.get("initial_stop_nick"),
                "protective_stop": breakout_exits.get("protective_stop_loss"),
                "take_profit": breakout_exits.get("take_profit"),
                "reward_risk_ratio_model": breakout_exits.get("reward_risk_ratio_model"),
            },
            "manual_review_required": not bool(daily.get("pass")),
            "review_note": "Daily setup is qualified. Prepare the next-session order plan and review gap, earnings, and duplicate orders before the open."
            if daily.get("pass")
            else "This is a monitoring candidate. Treat the order plan as a draft and confirm manually before use.",
        }

    @staticmethod
    def _next_weekday(current_date: date) -> date:
        candidate = current_date + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    @staticmethod
    def _format_session_label(session_dates: list[str]) -> str:
        if not session_dates:
            return "UNKNOWN"
        ordered = sorted(set(session_dates))
        return ordered[0] if len(ordered) == 1 else f"{ordered[0]} ~ {ordered[-1]}"

    def _apply_history_enhancement(
        self,
        candidates: list[dict],
        current_session_date: str,
        historical_candidates: list[dict] | None = None,
    ) -> None:
        historical_items = (
            historical_candidates
            if historical_candidates is not None
            else self.storage.get_recent_qualified_candidates(session_limit=HISTORY_SESSION_LIMIT)
        )
        sessions: dict[str, set[tuple[str, str]]] = {}
        candidate_sessions: dict[tuple[str, str], set[str]] = {}
        for item in historical_items:
            stored_session = item.get("stored_session_date", item.get("source_session_date"))
            if not stored_session or stored_session == current_session_date:
                continue
            key = (item["symbol"], item["direction"])
            session_key = str(stored_session)
            sessions.setdefault(session_key, set()).add(key)
            candidate_sessions.setdefault(key, set()).add(session_key)

        prior_session_dates = sorted(sessions.keys(), reverse=True)[: max(HISTORY_SESSION_LIMIT - 1, 0)]
        lookback_sessions = min(HISTORY_SESSION_LIMIT, 1 + len(prior_session_dates))
        for candidate in candidates:
            key = (candidate["symbol"], candidate["direction"])
            prior_dates = sorted(candidate_sessions.get(key, set()), reverse=True)
            consecutive_sessions = 1
            for session_date in prior_session_dates:
                if key not in sessions.get(session_date, set()):
                    break
                consecutive_sessions += 1

            appearance_count = 1 + len(prior_dates)
            history_bonus = round(min(len(prior_dates), HISTORY_SESSION_LIMIT - 1) * 0.25 + max(consecutive_sessions - 1, 0) * 0.15, 2)
            base_score = float(candidate.get("candidate_score", candidate.get("signal_score", 0.0)) or 0.0)
            tags = list(candidate.get("priority_tags", []))
            if consecutive_sessions >= 2:
                tags.append(f"Selected {consecutive_sessions} sessions in a row")
            elif appearance_count >= 2:
                tags.append(f"Selected {appearance_count} of last {lookback_sessions} sessions")
            tags = list(dict.fromkeys(tags))

            candidate["history"] = {
                "lookback_sessions": lookback_sessions,
                "appearance_count": appearance_count,
                "prior_appearance_count": len(prior_dates),
                "consecutive_sessions": consecutive_sessions,
                "session_dates": [current_session_date, *prior_dates],
            }
            candidate["history_score_bonus"] = history_bonus
            candidate["candidate_rank_score"] = round(base_score + history_bonus, 2)
            candidate["priority_tags"] = tags

    def _load_tracking_candidates(self) -> tuple[list[dict], str]:
        recent_candidates = self.storage.get_recent_qualified_candidates(session_limit=TRACKING_SESSION_LIMIT)
        deduped: dict[tuple[str, str], dict] = {}
        session_dates: list[str] = []
        for candidate in recent_candidates:
            key = (candidate["symbol"], candidate["direction"])
            if key in deduped:
                continue
            session_dates.append(candidate.get("stored_session_date", candidate.get("source_session_date", "UNKNOWN")))
            deduped[key] = candidate
        candidates = list(deduped.values())
        if candidates and session_dates:
            historical_candidates = self.storage.get_recent_qualified_candidates(session_limit=HISTORY_SESSION_LIMIT)
            self._apply_history_enhancement(candidates, max(session_dates), historical_candidates)
            candidates.sort(key=self._candidate_sort_key)
        return candidates, self._format_session_label(session_dates)

    def _classify_earnings_event(self, symbol: str, session_date: date, raw_event: dict | None) -> dict:
        if not raw_event or not raw_event.get("report_date"):
            return {
                "symbol": symbol,
                "report_date": None,
                "status": "UNKNOWN",
                "blocked": False,
                "warning": False,
                "days_until": None,
                "reason": "No earnings date was found",
            }

        try:
            report_date = datetime.fromisoformat(str(raw_event["report_date"])).date()
        except ValueError:
            return {
                "symbol": symbol,
                "report_date": None,
                "status": "UNKNOWN",
                "blocked": False,
                "warning": False,
                "days_until": None,
                "reason": f"Invalid earnings date format: {raw_event.get('report_date')}",
                "estimate": raw_event.get("estimate"),
            }
        days_until = (report_date - session_date).days
        if days_until < 0:
            return {
                "symbol": symbol,
                "report_date": None,
                "status": "UNKNOWN",
                "blocked": False,
                "warning": False,
                "days_until": None,
                "reason": f"Cached earnings date {report_date.isoformat()} is older than the current session; waiting for an updated date",
                "estimate": raw_event.get("estimate"),
            }

        blocked = -self.settings.qualification.earnings_block_days_after <= days_until <= self.settings.qualification.earnings_block_days_before
        warning = (
            not blocked
            and days_until is not None
            and 0 <= days_until <= self.settings.qualification.earnings_warn_days_before
        )

        if blocked:
            status = "BLOCKED"
            reason = f"Earnings date {report_date.isoformat()} is inside the blocked window"
        elif warning:
            status = "WARNING"
            reason = f"Earnings date {report_date.isoformat()} is near the earnings window"
        else:
            status = "CLEAR"
            reason = f"Next earnings date is {report_date.isoformat()}"

        return {
            "symbol": symbol,
            "report_date": report_date.isoformat(),
            "status": status,
            "blocked": blocked,
            "warning": warning,
            "days_until": days_until,
            "reason": reason,
            "estimate": raw_event.get("estimate"),
        }

    def _build_divergence_snapshot(self, weekly_frame, daily_frame, direction: str) -> dict:
        weekly_divergence = indicators.detect_divergence(
            weekly_frame,
            self.settings.strategy,
            direction,
            "Weekly",
            self.settings.qualification.strong_divergence_exhaustion_multiplier,
        )
        daily_divergence = indicators.detect_divergence(
            daily_frame,
            self.settings.strategy,
            direction,
            "Daily",
            self.settings.qualification.strong_divergence_exhaustion_multiplier,
        )
        strong_divergence = bool(
            (weekly_divergence.get("detected") and weekly_divergence.get("strong_alert"))
            or (daily_divergence.get("detected") and daily_divergence.get("strong_alert"))
        )
        return {
            "weekly": weekly_divergence,
            "daily": daily_divergence,
            "strong_divergence": strong_divergence,
        }

    def _build_open_position_earnings_summary(self, session_date: date) -> dict:
        open_trades = self.storage.list_open_trades()
        if not open_trades:
            return {
                "session_date": session_date.isoformat(),
                "window_days": self.settings.qualification.earnings_block_days_before,
                "total_positions": 0,
                "reminder_count": 0,
                "items": [],
            }

        symbols = sorted({str(item.get("stock", "")).strip().upper() for item in open_trades if item.get("stock")})
        earnings_map = self.earnings_calendar.get_upcoming_earnings(symbols, session_date=session_date)
        reminder_items: list[dict] = []

        for trade in open_trades:
            symbol = str(trade.get("stock", "")).strip().upper()
            if not symbol:
                continue

            earnings = self._classify_earnings_event(symbol, session_date, earnings_map.get(symbol))
            days_until = earnings.get("days_until")
            if days_until is None or not (0 <= int(days_until) <= self.settings.qualification.earnings_block_days_before):
                continue

            reminder_items.append(
                {
                    "trade_id": str(trade.get("id", "")),
                    "symbol": symbol,
                    "direction": str(trade.get("direction", "long")),
                    "report_date": earnings.get("report_date"),
                    "days_until": int(days_until),
                    "status": earnings.get("status", "UNKNOWN"),
                    "reason": earnings.get("reason", "No earnings date was found"),
                }
            )

        reminder_items.sort(key=lambda item: (item.get("days_until", 9999), item.get("symbol", "")))
        return {
            "session_date": session_date.isoformat(),
            "window_days": self.settings.qualification.earnings_block_days_before,
            "total_positions": len(open_trades),
            "reminder_count": len(reminder_items),
            "items": reminder_items,
        }

    def _build_position_health_summary(self, session_date: date) -> dict:
        """Build hourly impulse + divergence health check for all open positions."""
        open_trades = self.storage.list_open_trades()
        items: list[dict] = []

        for trade in open_trades:
            symbol = str(trade.get("stock", "")).strip().upper()
            direction = "SHORT" if str(trade.get("direction", "")).lower() == "short" else "LONG"
            if not symbol:
                continue
            try:
                hourly_frame = self.market_data.get_hourly_bars(symbol)
                hourly_impulse = indicators.calc_impulse_system(hourly_frame, self.settings.strategy) if hourly_frame is not None else {}
                hourly_color = hourly_impulse.get("color", "BLUE")

                divergence_direction = "LONG" if direction == "LONG" else "SHORT"
                divergence = indicators.detect_divergence(
                    hourly_frame,
                    self.settings.strategy,
                    divergence_direction,
                    "hourly",
                    exhaustion_multiplier=2.0,
                ) if hourly_frame is not None else {"detected": False}

                # Count consecutive RED/GREEN bars opposing the trade direction
                consecutive_opposing = 0
                if hourly_frame is not None and len(hourly_frame) >= 2 and hourly_color:
                    opposing_color = "RED" if direction == "LONG" else "GREEN"
                    for i in range(len(hourly_frame) - 1, max(len(hourly_frame) - 6, -1), -1):
                        bar_impulse = indicators.calc_impulse_system(hourly_frame.iloc[: i + 1], self.settings.strategy)
                        if bar_impulse.get("color") == opposing_color:
                            consecutive_opposing += 1
                        else:
                            break

                items.append(
                    {
                        "trade_id": str(trade.get("id", "")),
                        "symbol": symbol,
                        "direction": str(trade.get("direction", "long")),
                        "hourly_impulse_color": hourly_color,
                        "consecutive_opposing": consecutive_opposing,
                        "divergence_detected": bool(divergence.get("detected")),
                        "divergence_reason": divergence.get("reason", ""),
                    }
                )
            except Exception as exc:
                logger.warning("[%s] position health check failed: %s", symbol, exc)
                items.append(
                    {
                        "trade_id": str(trade.get("id", "")),
                        "symbol": symbol,
                        "direction": str(trade.get("direction", "long")),
                        "hourly_impulse_color": "UNKNOWN",
                        "consecutive_opposing": 0,
                        "divergence_detected": False,
                        "divergence_reason": f"Health check failed: {exc}",
                    }
                )

        return {
            "session_date": session_date.isoformat(),
            "total_positions": len(open_trades),
            "items": items,
        }

    def _build_open_position_exit_alert_summary(self, session_date: date) -> dict:
        open_trades = self.storage.list_open_trades()
        items: list[dict] = []

        for trade in open_trades:
            symbol = str(trade.get("stock", "")).strip().upper()
            direction = "SHORT" if str(trade.get("direction", "")).lower() == "short" else "LONG"
            if not symbol:
                continue
            try:
                weekly_frame = self.market_data.get_weekly_bars(symbol)
                daily_frame = self.market_data.get_daily_bars(symbol)
                weekly = self.model.screen_weekly(weekly_frame, self.settings.strategy)
                daily_impulse = indicators.calc_impulse_system(daily_frame, self.settings.strategy)
                weekly_color = weekly.get("impulse_color")
                daily_color = daily_impulse.get("color")
                weekly_opposes = (direction == "LONG" and weekly_color == "RED") or (
                    direction == "SHORT" and weekly_color == "GREEN"
                )
                daily_opposes = (direction == "LONG" and daily_color == "RED") or (
                    direction == "SHORT" and daily_color == "GREEN"
                )
                if weekly_opposes or daily_opposes:
                    items.append(
                        {
                            "trade_id": str(trade.get("id", "")),
                            "symbol": symbol,
                            "direction": str(trade.get("direction", "long")),
                            "weekly_impulse_color": weekly_color,
                            "weekly_trend": weekly.get("trend"),
                            "daily_impulse_color": daily_color,
                            "daily_ema_slope": daily_impulse.get("ema_slope"),
                            "daily_macd_slope": daily_impulse.get("macd_slope"),
                            "reason": "The model flags elevated loss risk for this open position; review whether it should be closed.",
                        }
                    )
            except Exception as exc:
                items.append(
                    {
                        "trade_id": str(trade.get("id", "")),
                        "symbol": symbol,
                        "direction": str(trade.get("direction", "long")),
                        "status": "ERROR",
                        "reason": f"Open-position impulse check failed: {exc}",
                    }
                )

        return {
            "session_date": session_date.isoformat(),
            "total_positions": len(open_trades),
            "alert_count": len(items),
            "items": items,
        }

    def _build_candidate(
        self,
        symbol: str,
        market_trend: str,
        session_date: date,
        earnings_map: dict[str, dict],
    ) -> dict | None:
        try:
            weekly_frame = self.market_data.get_weekly_bars(symbol)
            weekly = self.model.screen_weekly(weekly_frame, self.settings.strategy)
            if not weekly.get("actionable"):
                return None

            self.storage.upsert_weekly(
                symbol,
                weekly["macd"],
                weekly["macd_signal"],
                weekly["histogram"],
                weekly["histogram_prev"],
                weekly["confirmed_bars"],
                weekly["trend"],
            )

            if not weekly["pass"]:
                logger.info(
                    "[%s] skipped after weekly screen: %s | trend=%s confirmed_bars=%s checks={%s}",
                    symbol,
                    weekly.get("reason"),
                    weekly.get("trend"),
                    weekly.get("confirmed_bars"),
                    _format_check_map(weekly.get("pass_checks", {})),
                )
                return None

            direction = weekly["trend"]
            if self.settings.market_filter.enabled and direction == "LONG" and market_trend == "SHORT":
                return None

            daily_frame = self.market_data.get_daily_bars(symbol)
            daily = self.model.screen_daily(daily_frame, direction, self.settings.strategy)
            if not daily["pass"] and not daily.get("watch"):
                logger.info(
                    "[%s] skipped after daily screen: %s | state=%s entered_value_zone=%s value_zone_reached=%s "
                    "countertrend_exists=%s histogram_reversal=%s price_reversal=%s structure_intact=%s",
                    symbol,
                    daily.get("reason"),
                    daily.get("state"),
                    daily.get("entered_value_zone"),
                    daily.get("value_zone_reached"),
                    daily.get("countertrend_exists"),
                    daily.get("histogram_reversal", daily.get("momentum_reversal")),
                    daily.get("price_reversal"),
                    daily.get("structure_intact"),
                )
                return None
            self.storage.upsert_daily(symbol, daily["rsi"], daily["rsi_prev"], daily["rsi_state"])

            earnings = self._classify_earnings_event(symbol, session_date, earnings_map.get(symbol))
            daily["earnings_blocked"] = bool(earnings["blocked"])
            if earnings["blocked"]:
                logger.info("[%s] skipped because earnings window is blocked.", symbol)
                return None

            divergence = self._build_divergence_snapshot(weekly_frame, daily_frame, direction)
            divergence_detected = bool(divergence["weekly"].get("detected") or divergence["daily"].get("detected"))
            daily["priority_divergence"] = divergence_detected
            if daily.get("state") == "QUALIFIED" and divergence_detected:
                daily["state"] = "PRIORITY_QUALIFIED"
            candidate_score = indicators.calc_candidate_score(weekly, daily)
            order_plan = self._build_next_day_order_plan(
                symbol=symbol,
                direction=direction,
                session_date=session_date,
                daily_frame=daily_frame,
                weekly_frame=weekly_frame,
                daily=daily,
                hourly_frame=self.market_data.get_hourly_bars(symbol),
            )
            priority_tags = []
            if earnings["warning"]:
                priority_tags.append("EARNINGS_SOON")
            if divergence["weekly"].get("detected"):
                priority_tags.append("WEEKLY_DIVERGENCE")
            if divergence["daily"].get("detected"):
                priority_tags.append("DAILY_DIVERGENCE")
            if divergence["strong_divergence"]:
                priority_tags.append("STRONG_DIVERGENCE")

            candidate = {
                "symbol": symbol,
                "direction": direction,
                "source_session_date": session_date.isoformat(),
                "opportunity_status": "WATCHLIST" if daily.get("pass") else "MONITOR",
                "candidate_score": candidate_score,
                "execution_score": None,
                "signal_score": candidate_score,
                "reward_risk_score": 0.0,
                "weekly": weekly,
                "daily": daily,
                "hourly": {
                    "status": "PENDING_INTRADAY",
                    "reason": "EOD scan generated the next-session order plan. Hourly scans are reserved for touch alerts and breakout-quality review.",
                },
                "exits": order_plan.get("primary_order", {}).get("exits")
                or {
                    "reward_risk_ratio": 0.0,
                    "weekly_value_target": weekly.get("weekly_value_target"),
                },
                "order_plan": order_plan,
                "next_day_order_plan": order_plan,
                "earnings": earnings,
                "divergence": divergence,
                "strong_divergence": divergence["strong_divergence"],
                "priority_tags": priority_tags,
                "summary": f"{weekly['reason']} | {daily['reason']}",
            }
            logger.info(
                "[%s] qualified %s candidate_score=%.2f strong_div=%s | %s",
                symbol,
                "Long" if direction == "LONG" else "Short",
                candidate["candidate_score"],
                candidate["strong_divergence"],
                candidate["summary"],
            )
            return candidate
        except Exception as exc:
            logger.exception("[%s] failed during qualification: %s", symbol, exc)
            return None

    def _build_intraday_opportunity(self, candidate: dict) -> dict | None:
        symbol = candidate["symbol"]
        direction = candidate["direction"]
        try:
            if not candidate.get("daily", {}).get("pass"):
                return None

            weekly_frame = self.market_data.get_weekly_bars(symbol)
            daily_frame = self.market_data.get_daily_bars(symbol)
            daily = self.model.screen_daily(daily_frame, direction, self.settings.strategy)
            if not daily.get("pass"):
                return None

            hourly_frame = self.market_data.get_hourly_bars(symbol)
            intraday_plan = self.model.build_intraday_plan(
                direction=direction,
                daily_frame=daily_frame,
                weekly_frame=weekly_frame,
                hourly_frame=hourly_frame,
                settings=self.settings.strategy,
                trade_plan=self.settings.trade_plan,
                as_of=_utc_now(),
            )
            if intraday_plan is None or "close" not in intraday_plan.hourly:
                return None
            hourly = intraday_plan.hourly

            self.storage.upsert_hourly(
                symbol,
                hourly["close"],
                hourly["high_n"],
                hourly["low_n"],
                hourly["atr"],
                hourly["breakout_long"],
                hourly["breakout_short"],
            )

            exits = intraday_plan.exits
            opportunity = dict(candidate)
            opportunity["hourly"] = hourly
            opportunity["daily"] = daily
            opportunity["exits"] = exits
            opportunity["execution_score"] = indicators.calc_execution_score(
                candidate["weekly"],
                candidate["daily"],
                hourly,
                exits,
            )
            opportunity["signal_score"] = opportunity["execution_score"]
            opportunity["reward_risk_score"] = indicators.calc_reward_risk_score(
                float(exits.get("reward_risk_ratio_model", 0.0) or 0.0)
            )
            opportunity["opportunity_status"] = "TRIGGERED" if hourly["pass"] else "WATCHLIST"
            if not hourly["pass"] and (hourly.get("primary_entry_touched") or hourly.get("breakout_entry_touched")):
                opportunity["opportunity_status"] = "TOUCHED_ENTRY_PRICE"
            opportunity["cooldown_active"] = (not self.dry_run) and hourly["pass"] and self._is_recently_alerted(symbol, direction)
            opportunity["summary"] = (
                f"{candidate['weekly']['reason']} | {candidate['daily']['reason']} | {hourly['reason']}"
            )
            return opportunity
        except Exception as exc:
            logger.exception("[%s] failed during intraday scan: %s", symbol, exc)
            return None

    def _refresh_tracking_candidate(
        self,
        candidate: dict,
        market_trend: str,
        session_date: date,
        earnings_map: dict[str, dict],
    ) -> dict | None:
        symbol = candidate["symbol"]
        direction = candidate["direction"]
        try:
            weekly_frame = self.market_data.get_weekly_bars(symbol)
            weekly = self.model.screen_weekly(weekly_frame, self.settings.strategy)
            if not weekly.get("actionable") or not weekly.get("pass") or weekly.get("trend") != direction:
                logger.info("[%s] dropped from tracking after weekly refresh: %s", symbol, weekly.get("reason"))
                return None

            if self.settings.market_filter.enabled and direction == "LONG" and market_trend == "SHORT":
                logger.info("[%s] dropped from tracking because market filter blocks longs.", symbol)
                return None

            daily_frame = self.market_data.get_daily_bars(symbol)
            daily = self.model.screen_daily(daily_frame, direction, self.settings.strategy)
            if daily.get("state") == "REJECT":
                logger.info("[%s] dropped from tracking after daily refresh: %s", symbol, daily.get("reason"))
                return None

            earnings = self._classify_earnings_event(symbol, session_date, earnings_map.get(symbol))
            daily["earnings_blocked"] = bool(earnings["blocked"])
            if earnings["blocked"]:
                logger.info("[%s] dropped from tracking because earnings window is blocked.", symbol)
                return None

            divergence = self._build_divergence_snapshot(weekly_frame, daily_frame, direction)
            divergence_detected = bool(divergence["weekly"].get("detected") or divergence["daily"].get("detected"))
            daily["priority_divergence"] = divergence_detected
            if daily.get("state") == "QUALIFIED" and divergence_detected:
                daily["state"] = "PRIORITY_QUALIFIED"

            refreshed = dict(candidate)
            try:
                order_plan_session = date.fromisoformat(str(candidate.get("source_session_date") or session_date.isoformat()))
            except ValueError:
                order_plan_session = session_date
            order_plan = self._build_next_day_order_plan(
                symbol=symbol,
                direction=direction,
                session_date=order_plan_session,
                daily_frame=daily_frame,
                weekly_frame=weekly_frame,
                daily=daily,
                hourly_frame=self.market_data.get_hourly_bars(symbol),
            )
            refreshed["weekly"] = weekly
            refreshed["daily"] = daily
            refreshed["earnings"] = earnings
            refreshed["divergence"] = divergence
            refreshed["strong_divergence"] = divergence["strong_divergence"]
            refreshed["order_plan"] = order_plan
            refreshed["next_day_order_plan"] = order_plan
            refreshed["candidate_score"] = indicators.calc_candidate_score(weekly, daily)
            refreshed["signal_score"] = refreshed["candidate_score"]
            refreshed["execution_score"] = None
            refreshed["reward_risk_score"] = 0.0
            refreshed["opportunity_status"] = "WATCHLIST" if daily.get("pass") else "MONITOR"
            refreshed["summary"] = f"{weekly['reason']} | {daily['reason']}"
            refreshed["tracking_session_date"] = session_date.isoformat()
            return refreshed
        except Exception as exc:
            logger.exception("[%s] failed during tracking refresh: %s", symbol, exc)
            return None

    def run_end_of_day_scan(self) -> list[dict]:
        started_at = time.time()
        session_date = self._latest_completed_session_date()
        logger.info("==================================================")
        logger.info("end-of-day qualification started for %s", session_date.isoformat())
        if self.dry_run:
            logger.info("dry-run enabled: notifications and alert-log updates are suppressed")

        symbol_rows = self._load_universe()
        symbols = [row["symbol"] for row in symbol_rows]
        benchmark_symbol = self.settings.market_filter.benchmark_symbol if self.settings.market_filter.enabled else None
        self.market_data.warm_cache_for_scan(symbols, benchmark_symbol=benchmark_symbol)

        market_trend = self._check_market_trend()
        earnings_map = self.earnings_calendar.get_upcoming_earnings(symbols, session_date=session_date)
        logger.info("market trend: %s | earnings events loaded for %s symbols", market_trend, len(earnings_map))

        candidates: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.settings.runtime.max_workers) as executor:
            futures = {
                executor.submit(self._build_candidate, symbol, market_trend, session_date, earnings_map): symbol
                for symbol in symbols
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    candidates.append(result)

        self._apply_history_enhancement(candidates, session_date.isoformat())
        candidates.sort(key=self._candidate_sort_key)
        self.storage.replace_qualified_candidates(session_date.isoformat(), candidates)
        if self.dry_run:
            stop_update_summary = self.journal_manager.preview_open_position_stops(session_date=session_date)
        else:
            stop_update_summary = self.journal_manager.update_open_position_stops(session_date=session_date)
        open_position_earnings_summary = self._build_open_position_earnings_summary(session_date)
        open_position_exit_alert_summary = self._build_open_position_exit_alert_summary(session_date)
        position_health_summary = self._build_position_health_summary(session_date)

        display_limit = max(self.settings.alerts.qualified_display_limit, 0)
        displayed_candidates = candidates[:display_limit] if display_limit else []
        strong_divergence_count = sum(1 for item in candidates if item.get("strong_divergence"))

        for index, candidate in enumerate(displayed_candidates, start=1):
            logger.info(
                "QUALIFIED TOP %s [%s] %s %s candidate_score=%.2f strong_div=%s | %s",
                index,
                candidate["earnings"]["status"],
                candidate["symbol"],
                "Long" if candidate["direction"] == "LONG" else "Short",
                candidate.get("candidate_score", candidate.get("signal_score", 0.0)),
                candidate["strong_divergence"],
                candidate["summary"],
            )

        elapsed = time.time() - started_at
        if not self.dry_run:
            self.notifier.send_candidate_summary(
                displayed_candidates,
                len(candidates),
                session_date.isoformat(),
                elapsed,
                stop_update_summary={
                    "session_date": stop_update_summary.session_date,
                    "total_positions": stop_update_summary.total_positions,
                    "updated_count": stop_update_summary.updated_count,
                    "unchanged_count": stop_update_summary.unchanged_count,
                    "error_count": stop_update_summary.error_count,
                    "updates": stop_update_summary.updates,
                },
                open_position_earnings_summary=open_position_earnings_summary,
                open_position_exit_alert_summary=open_position_exit_alert_summary,
                position_health_summary=position_health_summary,
            )

        logger.info(
            "end-of-day qualification finished: %s qualified, %s displayed, %s strong divergences, %s stop updates, elapsed %.1fs",
            len(candidates),
            len(displayed_candidates),
            strong_divergence_count,
            stop_update_summary.updated_count,
            elapsed,
        )
        return candidates

    def run_intraday_scan(self) -> list[dict]:
        started_at = time.time()
        session_date = self._latest_completed_session_date()
        candidate_session = session_date.isoformat()
        logger.info("==================================================")
        logger.info("intraday trigger scan started using latest completed session %s", candidate_session)
        if self.dry_run:
            logger.info("dry-run enabled: notifications and alert-log updates are suppressed")

        tracking_candidates, tracking_label = self._load_tracking_candidates()
        if not tracking_candidates:
            latest_available_session = self.storage.get_latest_candidate_session()
            logger.warning(
                "no stored qualified candidates found for tracking; latest available session is %s",
                latest_available_session,
            )
            return []

        symbols = [item["symbol"] for item in tracking_candidates]
        benchmark_symbol = self.settings.market_filter.benchmark_symbol if self.settings.market_filter.enabled else None
        self.market_data.warm_cache_for_scan(symbols, benchmark_symbol=benchmark_symbol)

        market_trend = self._check_market_trend()
        earnings_map = self.earnings_calendar.get_upcoming_earnings(
            symbols,
            session_date=session_date,
        )
        candidates: list[dict] = []
        for candidate in tracking_candidates:
            refreshed = self._refresh_tracking_candidate(candidate, market_trend, session_date, earnings_map)
            if refreshed:
                candidates.append(refreshed)
        if not candidates:
            logger.info("no active tracking candidates remain after weekly/daily refresh.")
            return []

        opportunities: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.settings.runtime.max_workers) as executor:
            futures = {executor.submit(self._build_intraday_opportunity, candidate): candidate["symbol"] for candidate in candidates}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    opportunities.append(result)

        opportunities.sort(key=self._triggered_sort_key)
        actionable = [
            item
            for item in opportunities
            if item["opportunity_status"] in {"TRIGGERED", "TOUCHED_ENTRY_PRICE"}
        ]
        top_triggered = actionable

        for index, opportunity in enumerate(top_triggered, start=1):
            logger.info(
                "INTRADAY ACTION TOP %s %s %s status=%s execution_score=%.2f rr=%.2f strong_div=%s | %s",
                index,
                opportunity["symbol"],
                "Long" if opportunity["direction"] == "LONG" else "Short",
                opportunity["opportunity_status"],
                opportunity.get("execution_score", opportunity.get("signal_score", 0.0)),
                float(opportunity["exits"].get("reward_risk_ratio_model", 0.0) or 0.0),
                opportunity.get("strong_divergence"),
                opportunity["summary"],
            )

        # Divergence scan for open positions — send immediate alert (separate from entry triggers)
        if not self.dry_run:
            open_trades = self.storage.list_open_trades()
            for trade in open_trades:
                trade_symbol = str(trade.get("stock", "")).strip().upper()
                trade_direction = "SHORT" if str(trade.get("direction", "")).lower() == "short" else "LONG"
                if not trade_symbol:
                    continue
                try:
                    hourly_frame = self.market_data.get_hourly_bars(trade_symbol)
                    if hourly_frame is None or hourly_frame.empty:
                        continue
                    divergence = indicators.detect_divergence(
                        hourly_frame,
                        self.settings.strategy,
                        trade_direction,
                        "hourly",
                        exhaustion_multiplier=2.0,
                    )
                    if divergence.get("detected") and not self._is_divergence_recently_alerted(trade_symbol, trade_direction):
                        self.notifier.send_divergence_alert(
                            trade_symbol,
                            trade_direction,
                            divergence.get("reason", ""),
                        )
                        self.storage.update_divergence_alert_log(trade_symbol, trade_direction)
                        logger.info("[%s] divergence alert sent for open position", trade_symbol)
                except Exception as exc:
                    logger.warning("[%s] divergence check failed during intraday scan: %s", trade_symbol, exc)

        elapsed = time.time() - started_at
        logger.info(
            "intraday trigger scan finished: %s candidates scanned, %s actionable (entry notifications suppressed), elapsed %.1fs",
            len(candidates),
            len(top_triggered),
            elapsed,
        )
        return top_triggered

    def _latest_reference_price(self, symbol: str) -> tuple[float | None, str]:
        hourly_frame = self.market_data.get_hourly_bars(symbol)
        if hourly_frame is not None and not hourly_frame.empty:
            return round(float(hourly_frame["close"].iloc[-1]), 4), "latest_hourly_close"
        daily_frame = self.market_data.get_daily_bars(symbol)
        if daily_frame is not None and not daily_frame.empty:
            return round(float(daily_frame["close"].iloc[-1]), 4), "latest_daily_close"
        return None, "unavailable"

    @staticmethod
    def _has_active_manual_order(orders: list[dict], symbol: str, direction: str) -> bool:
        inactive_statuses = {"CANCELLED", "CANCELED", "FILLED", "INACTIVE", "REJECTED", "EXPIRED"}
        for order in orders:
            if str(order.get("symbol", "")).upper() != symbol:
                continue
            if str(order.get("direction", "")).upper() != direction:
                continue
            if str(order.get("status", "")).upper() in inactive_statuses:
                continue
            return True
        return False

    def _build_premarket_review_item(self, candidate: dict, manual_orders: list[dict]) -> dict:
        symbol = candidate["symbol"]
        direction = candidate["direction"]
        order_plan = candidate.get("order_plan") or candidate.get("next_day_order_plan") or {}
        primary_order = order_plan.get("primary_order") or {}
        stop_price = primary_order.get("stop_price")
        limit_price = primary_order.get("limit_price")
        current_price, price_source = self._latest_reference_price(symbol)
        checks: list[dict] = []

        has_order = self._has_active_manual_order(manual_orders, symbol, direction)
        checks.append(
            {
                "code": "MANUAL_ORDER",
                "pass": has_order,
                "severity": "WARN" if not has_order else "OK",
                "message": "Manual order record found" if has_order else "No manual order record found. Confirm whether the order exists in IBKR.",
            }
        )
        checks.append(
            {
                "code": "EARNINGS",
                "pass": not bool(candidate.get("earnings", {}).get("blocked")),
                "severity": "BLOCK" if candidate.get("earnings", {}).get("blocked") else "OK",
                "message": candidate.get("earnings", {}).get("reason", "Earnings status is clear"),
            }
        )
        checks.append(
            {
                "code": "CANDIDATE",
                "pass": bool(candidate.get("daily", {}).get("pass")),
                "severity": "WARN" if not candidate.get("daily", {}).get("pass") else "OK",
                "message": "Candidate remains qualified" if candidate.get("daily", {}).get("pass") else "Daily setup is no longer an immediate execution candidate",
            }
        )

        gap_message = "No premarket reference price is available, so the gap cannot be evaluated"
        gap_pass = True
        gap_pct = None
        if current_price is not None and stop_price is not None:
            stop_value = float(stop_price)
            gap_pct = ((float(current_price) - stop_value) / stop_value) * 100
            if direction == "LONG":
                gap_pass = float(current_price) <= float(limit_price or stop_value) and gap_pct <= PREMARKET_GAP_ALERT_PCT * 100
                gap_message = (
                    f"Reference price {current_price:.2f} is above the acceptable limit {float(limit_price):.2f}; review manually"
                    if not gap_pass and limit_price is not None and float(current_price) > float(limit_price)
                    else f"Reference price is {gap_pct:.2f}% away from the breakout stop"
                )
            else:
                gap_pass = float(current_price) >= float(limit_price or stop_value) and gap_pct >= -PREMARKET_GAP_ALERT_PCT * 100
                gap_message = (
                    f"Reference price {current_price:.2f} is below the acceptable limit {float(limit_price):.2f}; review manually"
                    if not gap_pass and limit_price is not None and float(current_price) < float(limit_price)
                    else f"Reference price is {gap_pct:.2f}% away from the breakout stop"
                )
        checks.append(
            {
                "code": "GAP",
                "pass": gap_pass,
                "severity": "WARN" if not gap_pass else "OK",
                "message": gap_message,
            }
        )

        blocked = any(check["severity"] == "BLOCK" for check in checks)
        warning = any(check["severity"] == "WARN" for check in checks)
        status = "BLOCKED" if blocked else "REVIEW" if warning else "READY"
        return {
            "symbol": symbol,
            "direction": direction,
            "status": status,
            "current_reference_price": current_price,
            "price_source": price_source,
            "gap_pct": round(gap_pct, 2) if gap_pct is not None else None,
            "order_plan": order_plan,
            "manual_orders": [
                order
                for order in manual_orders
                if str(order.get("symbol", "")).upper() == symbol and str(order.get("direction", "")).upper() == direction
            ],
            "checks": checks,
        }

    def run_premarket_review(self) -> list[dict]:
        started_at = time.time()
        candidate_session_date = self._latest_completed_session_date()
        now_local = self._market_now()
        review_date = now_local.date() if now_local.weekday() < 5 else candidate_session_date
        logger.info("==================================================")
        logger.info(
            "premarket order review started for candidate session %s on review date %s",
            candidate_session_date.isoformat(),
            review_date.isoformat(),
        )

        tracking_candidates, tracking_label = self._load_tracking_candidates()
        if not tracking_candidates:
            logger.warning("no stored qualified candidates found for premarket review.")
            return []

        symbols = [item["symbol"] for item in tracking_candidates]
        benchmark_symbol = self.settings.market_filter.benchmark_symbol if self.settings.market_filter.enabled else None
        self.market_data.warm_cache_for_scan(symbols, benchmark_symbol=benchmark_symbol)
        market_trend = self._check_market_trend()
        earnings_map = self.earnings_calendar.get_upcoming_earnings(symbols, session_date=review_date)
        manual_orders = self.storage.list_planned_orders(tracking_label if "~" not in tracking_label else None)

        refreshed_candidates: list[dict] = []
        for candidate in tracking_candidates:
            refreshed = self._refresh_tracking_candidate(candidate, market_trend, review_date, earnings_map)
            if refreshed:
                refreshed_candidates.append(refreshed)

        review_items = [self._build_premarket_review_item(candidate, manual_orders) for candidate in refreshed_candidates]
        elapsed = time.time() - started_at
        if not self.dry_run:
            self.notifier.send_premarket_review_summary(review_items, tracking_label, elapsed)
        logger.info(
            "premarket order review finished: %s candidates reviewed, elapsed %.1fs",
            len(review_items),
            elapsed,
        )
        return review_items

    def run_scan(self, mode: str = "auto") -> list[dict]:
        normalized_mode = (mode or "auto").strip().lower()
        if normalized_mode == "eod":
            return self.run_end_of_day_scan()
        if normalized_mode == "intraday":
            return self.run_intraday_scan()
        if normalized_mode == "premarket":
            return self.run_premarket_review()
        if normalized_mode == "full":
            self.run_end_of_day_scan()
            return self.run_intraday_scan()

        now_local = self._market_now()
        if self._is_market_open(now_local):
            return self.run_intraday_scan()
        premarket_review_start_time = getattr(self, "premarket_review_start_time", clock_time(hour=9, minute=15))
        if now_local.weekday() < 5 and premarket_review_start_time <= now_local.time() < self.market_open_time:
            return self.run_premarket_review()
        if now_local.weekday() < 5 and self.market_close_time <= now_local.time() <= self.eod_auto_cutoff_time:
            return self.run_end_of_day_scan()
        logger.info("auto scan skipped outside market hours and EOD window: %s", now_local.isoformat())
        return []
