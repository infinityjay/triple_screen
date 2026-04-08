from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

import indicators
from alpaca import AlpacaClient
from earnings import EarningsCalendarClient
from journal import JournalManager
from schema import AppConfig
from sqlite import SQLiteStorage
from telegram import TelegramNotifier

logger = logging.getLogger(__name__)


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
        self.journal_manager = JournalManager(
            storage=storage,
            market_data=market_data,
            trade_plan=settings.trade_plan,
        )
        self.market_timezone = ZoneInfo(settings.app.timezone)
        self.market_open_time = clock_time(hour=9, minute=30)
        self.market_close_time = clock_time(hour=16, minute=0)

    def _is_recently_alerted(self, symbol: str, direction: str) -> bool:
        row = self.storage.get_last_alert(symbol)
        if not row:
            return False
        last_alert_at = datetime.fromisoformat(row["last_alert_at"])
        cooldown = timedelta(hours=self.settings.alerts.cooldown_hours)
        return datetime.utcnow() - last_alert_at <= cooldown and row["last_direction"] == direction

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
        result = indicators.screen_weekly(frame, self.settings.strategy)
        return result.get("trend", "UNKNOWN")

    @staticmethod
    def _candidate_sort_key(item: dict) -> tuple[float, int, int]:
        status_priority = 0 if item["opportunity_status"] == "TRIGGERED" else 1
        return (-item.get("candidate_score", item.get("signal_score", 0.0)), -int(bool(item.get("strong_divergence"))), status_priority)

    @staticmethod
    def _triggered_sort_key(item: dict) -> tuple[float, int]:
        return (-item.get("execution_score", item.get("signal_score", 0.0)), -int(bool(item.get("strong_divergence"))))

    @staticmethod
    def _format_session_label(session_dates: list[str]) -> str:
        if not session_dates:
            return "UNKNOWN"
        ordered = sorted(set(session_dates))
        return ordered[0] if len(ordered) == 1 else f"{ordered[0]} ~ {ordered[-1]}"

    def _load_tracking_candidates(self) -> tuple[list[dict], str]:
        recent_candidates = self.storage.get_recent_qualified_candidates(session_limit=5)
        deduped: dict[tuple[str, str], dict] = {}
        session_dates: list[str] = []
        for candidate in recent_candidates:
            key = (candidate["symbol"], candidate["direction"])
            if key in deduped:
                continue
            session_dates.append(candidate.get("stored_session_date", candidate.get("source_session_date", "UNKNOWN")))
            deduped[key] = candidate
        return list(deduped.values()), self._format_session_label(session_dates)

    def _classify_earnings_event(self, symbol: str, session_date: date, raw_event: dict | None) -> dict:
        if not raw_event or not raw_event.get("report_date"):
            return {
                "symbol": symbol,
                "report_date": None,
                "status": "UNKNOWN",
                "blocked": False,
                "warning": False,
                "days_until": None,
                "reason": "未获取到财报日期",
            }

        report_date = datetime.fromisoformat(str(raw_event["report_date"])).date()
        days_until = (report_date - session_date).days
        blocked = -self.settings.qualification.earnings_block_days_after <= days_until <= self.settings.qualification.earnings_block_days_before
        warning = (
            not blocked
            and days_until is not None
            and 0 <= days_until <= self.settings.qualification.earnings_warn_days_before
        )

        if blocked:
            status = "BLOCKED"
            reason = f"财报日在 {report_date.isoformat()}，处于黑窗期"
        elif warning:
            status = "WARNING"
            reason = f"财报日在 {report_date.isoformat()}，接近财报窗口"
        else:
            status = "CLEAR"
            reason = f"下一次财报日为 {report_date.isoformat()}"

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
            "周线",
            self.settings.qualification.strong_divergence_exhaustion_multiplier,
        )
        daily_divergence = indicators.detect_divergence(
            daily_frame,
            self.settings.strategy,
            direction,
            "日线",
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

    def _build_candidate(
        self,
        symbol: str,
        market_trend: str,
        session_date: date,
        earnings_map: dict[str, dict],
    ) -> dict | None:
        try:
            weekly_frame = self.market_data.get_weekly_bars(symbol)
            weekly = indicators.screen_weekly(weekly_frame, self.settings.strategy)
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
            daily = indicators.screen_daily(daily_frame, direction, self.settings.strategy)
            if not daily["pass"]:
                logger.info(
                    "[%s] skipped after daily screen: %s | state=%s rsi=%.2f rsi_in_value_zone=%s entered_value_zone=%s value_zone_reached=%s "
                    "countertrend_exists=%s momentum_reversal=%s price_reversal=%s structure_intact=%s",
                    symbol,
                    daily.get("reason"),
                    daily.get("state"),
                    float(daily.get("rsi", 0.0)),
                    daily.get("rsi_in_value_zone"),
                    daily.get("entered_value_zone"),
                    daily.get("value_zone_reached"),
                    daily.get("countertrend_exists"),
                    daily.get("momentum_reversal"),
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
                "opportunity_status": "WATCHLIST",
                "candidate_score": candidate_score,
                "execution_score": None,
                "signal_score": candidate_score,
                "reward_risk_score": 0.0,
                "weekly": weekly,
                "daily": daily,
                "hourly": {
                    "status": "PENDING_INTRADAY",
                    "reason": "收盘候选池阶段不计算小时线触发，留待下一交易日盘中扫描。",
                },
                "exits": {
                    "reward_risk_ratio": 0.0,
                },
                "earnings": earnings,
                "divergence": divergence,
                "strong_divergence": divergence["strong_divergence"],
                "priority_tags": priority_tags,
                "summary": f"{weekly['reason']} | {daily['reason']}",
            }
            logger.info(
                "[%s] qualified %s candidate_score=%.2f strong_div=%s | %s",
                symbol,
                "做多" if direction == "LONG" else "做空",
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
            daily_frame = self.market_data.get_daily_bars(symbol)
            hourly_frame = self.market_data.get_hourly_bars(symbol)
            hourly = indicators.screen_hourly(hourly_frame, direction, self.settings.strategy, as_of=datetime.utcnow())
            if "close" not in hourly:
                return None

            self.storage.upsert_hourly(
                symbol,
                hourly["close"],
                hourly["high_n"],
                hourly["low_n"],
                hourly["atr"],
                hourly["breakout_long"],
                hourly["breakout_short"],
            )

            exits = indicators.calc_exits(
                direction,
                hourly["entry_price"],
                daily_frame,
                hourly["atr"],
                self.settings.trade_plan,
            )
            if exits["reward_risk_ratio"] < self.settings.qualification.intraday_minimum_reward_risk:
                logger.info(
                    "[%s] skipped intraday because reward/risk %.2f < %.2f",
                    symbol,
                    exits["reward_risk_ratio"],
                    self.settings.qualification.intraday_minimum_reward_risk,
                )
                return None

            opportunity = dict(candidate)
            opportunity["hourly"] = hourly
            opportunity["exits"] = exits
            opportunity["execution_score"] = indicators.calc_execution_score(
                candidate["weekly"],
                candidate["daily"],
                hourly,
                exits,
            )
            opportunity["signal_score"] = opportunity["execution_score"]
            opportunity["reward_risk_score"] = indicators.calc_reward_risk_score(exits["reward_risk_ratio"])
            opportunity["opportunity_status"] = "TRIGGERED" if hourly["pass"] else "WATCHLIST"
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
            weekly = indicators.screen_weekly(weekly_frame, self.settings.strategy)
            if not weekly.get("actionable") or not weekly.get("pass") or weekly.get("trend") != direction:
                logger.info("[%s] dropped from tracking after weekly refresh: %s", symbol, weekly.get("reason"))
                return None

            if self.settings.market_filter.enabled and direction == "LONG" and market_trend == "SHORT":
                logger.info("[%s] dropped from tracking because market filter blocks longs.", symbol)
                return None

            daily_frame = self.market_data.get_daily_bars(symbol)
            daily = indicators.screen_daily(daily_frame, direction, self.settings.strategy)
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
            refreshed["weekly"] = weekly
            refreshed["daily"] = daily
            refreshed["earnings"] = earnings
            refreshed["divergence"] = divergence
            refreshed["strong_divergence"] = divergence["strong_divergence"]
            refreshed["candidate_score"] = indicators.calc_candidate_score(weekly, daily)
            refreshed["signal_score"] = refreshed["candidate_score"]
            refreshed["execution_score"] = None
            refreshed["reward_risk_score"] = 0.0
            refreshed["opportunity_status"] = "WATCHLIST"
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

        candidates.sort(key=self._candidate_sort_key)
        self.storage.replace_qualified_candidates(session_date.isoformat(), candidates)
        if self.dry_run:
            stop_update_summary = self.journal_manager.preview_open_position_stops(session_date=session_date)
        else:
            stop_update_summary = self.journal_manager.update_open_position_stops(session_date=session_date)

        display_limit = max(self.settings.alerts.qualified_display_limit, 0)
        displayed_candidates = candidates[:display_limit] if display_limit else []
        strong_divergence_count = sum(1 for item in candidates if item.get("strong_divergence"))

        for index, candidate in enumerate(displayed_candidates, start=1):
            logger.info(
                "QUALIFIED TOP %s [%s] %s %s candidate_score=%.2f strong_div=%s | %s",
                index,
                candidate["earnings"]["status"],
                candidate["symbol"],
                "做多" if candidate["direction"] == "LONG" else "做空",
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
            if not self.dry_run:
                elapsed = time.time() - started_at
                self.notifier.send_trigger_summary([], tracking_label, 0, elapsed)
            return []

        opportunities: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.settings.runtime.max_workers) as executor:
            futures = {executor.submit(self._build_intraday_opportunity, candidate): candidate["symbol"] for candidate in candidates}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    opportunities.append(result)

        triggered_limit = max(self.settings.alerts.max_triggered_signals_per_scan, 0)
        opportunities.sort(key=self._triggered_sort_key)
        triggered = [item for item in opportunities if item["opportunity_status"] == "TRIGGERED"]
        top_triggered = triggered[:triggered_limit] if triggered_limit else []

        for index, opportunity in enumerate(top_triggered, start=1):
            logger.info(
                "TRIGGERED TOP %s %s %s execution_score=%.2f rr=%.2f strong_div=%s | %s",
                index,
                opportunity["symbol"],
                "做多" if opportunity["direction"] == "LONG" else "做空",
                opportunity.get("execution_score", opportunity.get("signal_score", 0.0)),
                opportunity["exits"]["reward_risk_ratio"],
                opportunity.get("strong_divergence"),
                opportunity["summary"],
            )

        elapsed = time.time() - started_at
        if not self.dry_run:
            self.notifier.send_trigger_summary(top_triggered, tracking_label or "UNKNOWN", len(candidates), elapsed)
            time.sleep(1)
            for index, opportunity in enumerate(top_triggered, start=1):
                if opportunity.get("cooldown_active"):
                    continue
                payload = dict(opportunity)
                payload["rank"] = index
                payload["total_ranked"] = len(top_triggered)
                payload["rank_group"] = "TRIGGERED"
                self.notifier.send_signal(payload)
                self.storage.update_alert_log(opportunity["symbol"], opportunity["direction"])
                time.sleep(1)

        logger.info(
            "intraday trigger scan finished: %s candidates scanned, %s triggered, elapsed %.1fs",
            len(candidates),
            len(top_triggered),
            elapsed,
        )
        return top_triggered

    def run_scan(self, mode: str = "auto") -> list[dict]:
        normalized_mode = (mode or "auto").strip().lower()
        if normalized_mode == "eod":
            return self.run_end_of_day_scan()
        if normalized_mode == "intraday":
            return self.run_intraday_scan()
        if normalized_mode == "full":
            self.run_end_of_day_scan()
            return self.run_intraday_scan()

        now_local = self._market_now()
        if self._is_market_open(now_local):
            return self.run_intraday_scan()
        if now_local.weekday() < 5 and now_local.time() >= self.market_close_time:
            return self.run_end_of_day_scan()
        return self.run_intraday_scan()
