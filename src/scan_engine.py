from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import indicators
from alpaca import AlpacaClient
from schema import AppConfig
from sqlite import SQLiteStorage
from telegram import TelegramNotifier

logger = logging.getLogger(__name__)


class TripleScreenScanner:
    def __init__(
        self,
        settings: AppConfig,
        market_data: AlpacaClient,
        storage: SQLiteStorage,
        notifier: TelegramNotifier,
        dry_run: bool = False,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.storage = storage
        self.notifier = notifier
        self.dry_run = dry_run

    def _is_recently_alerted(self, symbol: str, direction: str) -> bool:
        row = self.storage.get_last_alert(symbol)
        if not row:
            return False
        last_alert_at = datetime.fromisoformat(row["last_alert_at"])
        cooldown = timedelta(hours=self.settings.alerts.cooldown_hours)
        return datetime.utcnow() - last_alert_at <= cooldown and row["last_direction"] == direction

    def _check_market_trend(self) -> str:
        if not self.settings.market_filter.enabled:
            return "UNKNOWN"

        frame = self.market_data.get_weekly_bars(self.settings.market_filter.benchmark_symbol)
        result = indicators.screen_weekly(frame, self.settings.strategy)
        return result.get("trend", "UNKNOWN")

    @staticmethod
    def _opportunity_sort_key(item: dict) -> tuple[int, float]:
        status_priority = 0 if item["opportunity_status"] == "TRIGGERED" else 1
        return (status_priority, -item["signal_score"])

    def _build_opportunity(
        self,
        symbol: str,
        trend: str,
        weekly: dict,
        daily: dict,
        hourly: dict,
        daily_frame,
    ) -> dict:
        exits = indicators.calc_exits(
            trend,
            hourly["entry_price"],
            daily_frame,
            hourly["atr"],
            self.settings.trade_plan,
        )
        score = indicators.calc_signal_score(weekly, daily, hourly)
        opportunity_status = "TRIGGERED" if hourly["pass"] else "WATCHLIST"
        cooldown_active = (not self.dry_run) and hourly["pass"] and self._is_recently_alerted(symbol, trend)

        return {
            "symbol": symbol,
            "direction": trend,
            "opportunity_status": opportunity_status,
            "cooldown_active": cooldown_active,
            "signal_score": score,
            "weekly": weekly,
            "daily": daily,
            "hourly": hourly,
            "exits": exits,
            "summary": f"{weekly['reason']} | {daily['reason']} | {hourly['reason']}",
        }

    def _process_symbol(self, symbol: str, market_trend: str) -> dict | None:
        try:
            weekly_frame = self.market_data.get_weekly_bars(symbol)
            weekly = indicators.screen_weekly(weekly_frame, self.settings.strategy)
            if not weekly.get("actionable"):
                return None

            trend = weekly["trend"]
            if self.settings.market_filter.enabled and trend == "LONG" and market_trend == "SHORT":
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

            daily_frame = self.market_data.get_daily_bars(symbol)
            daily = indicators.screen_daily(daily_frame, trend, self.settings.strategy)
            if not daily["pass"]:
                return None

            self.storage.upsert_daily(symbol, daily["rsi"], daily["rsi_prev"], daily["rsi_state"])

            hourly_frame = self.market_data.get_hourly_bars(symbol)
            hourly = indicators.screen_hourly(hourly_frame, trend, self.settings.strategy)
            if "close" not in hourly:
                logger.info("[%s] skipped after daily setup because hourly data is insufficient.", symbol)
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

            opportunity = self._build_opportunity(symbol, trend, weekly, daily, hourly, daily_frame)

            if hourly["pass"] and not opportunity["cooldown_active"]:
                self.storage.save_signal(
                    symbol,
                    trend,
                    hourly["entry_price"],
                    opportunity["exits"]["stop_loss_safezone"],
                    opportunity["exits"]["stop_loss_two_bar"],
                    opportunity["exits"]["take_profit"],
                    opportunity["signal_score"],
                    weekly["histogram"],
                    weekly["trend"],
                    daily["rsi"],
                    hourly["close"],
                    hourly["atr"],
                    None,
                )

            logger.info(
                "[%s] %s %s score=%.2f | %s",
                symbol,
                "做多" if trend == "LONG" else "做空",
                "已触发" if opportunity["opportunity_status"] == "TRIGGERED" else "待触发",
                opportunity["signal_score"],
                opportunity["summary"],
            )
            if opportunity["cooldown_active"]:
                logger.info("[%s] triggered but alert cooldown is active.", symbol)
            return opportunity
        except Exception as exc:
            logger.exception("[%s] failed during processing: %s", symbol, exc)
            return None

    def run_scan(self) -> list[dict]:
        started_at = time.time()
        logger.info("==================================================")
        logger.info("scan started at %s", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
        if self.dry_run:
            logger.info("dry-run enabled: notifications and alert-log updates are suppressed")

        symbol_rows = self.market_data.get_top_symbols(self.settings.universe)
        for row in symbol_rows:
            self.storage.upsert_symbol(row["symbol"], row.get("market_cap"), row.get("sector"))
        symbols = [row["symbol"] for row in symbol_rows]

        logger.info("universe loaded: %s symbols", len(symbols))

        benchmark_symbol = self.settings.market_filter.benchmark_symbol if self.settings.market_filter.enabled else None
        self.market_data.warm_cache_for_scan(symbols, benchmark_symbol=benchmark_symbol)

        market_trend = self._check_market_trend()
        logger.info("market trend: %s", market_trend)

        opportunities: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.settings.runtime.max_workers) as executor:
            futures = {executor.submit(self._process_symbol, symbol, market_trend): symbol for symbol in symbols}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    opportunities.append(result)

        top_limit = min(3, self.settings.alerts.max_signals_per_scan)
        opportunities.sort(key=self._opportunity_sort_key)
        top_opportunities = opportunities[:top_limit]
        triggered_opportunities = [item for item in top_opportunities if item["opportunity_status"] == "TRIGGERED"]
        watchlist_count = sum(1 for item in top_opportunities if item["opportunity_status"] == "WATCHLIST")

        for index, opportunity in enumerate(top_opportunities, start=1):
            logger.info(
                "TOP %s [%s] %s %s score=%.2f entry=%.2f | %s",
                index,
                "已触发" if opportunity["opportunity_status"] == "TRIGGERED" else "待触发",
                opportunity["symbol"],
                "做多" if opportunity["direction"] == "LONG" else "做空",
                opportunity["signal_score"],
                opportunity["exits"]["entry"],
                opportunity["summary"],
            )

        if self.dry_run:
            logger.info(
                "dry-run top opportunities=%s, triggered alerts=%s, watchlist=%s",
                len(top_opportunities),
                len(triggered_opportunities),
                watchlist_count,
            )

        elapsed = time.time() - started_at
        if not self.dry_run:
            if not top_opportunities:
                self.notifier.send_no_opportunity(elapsed)
            else:
                for index, opportunity in enumerate(top_opportunities, start=1):
                    payload = dict(opportunity)
                    payload["rank"] = index
                    payload["total_ranked"] = len(top_opportunities)
                    self.notifier.send_signal(payload)
                    if opportunity["opportunity_status"] == "TRIGGERED" and not opportunity["cooldown_active"]:
                        self.storage.update_alert_log(opportunity["symbol"], opportunity["direction"])
                    time.sleep(1)
        logger.info(
            "scan finished: %s opportunities, %s top-ranked, %s triggered alerts, elapsed %.1fs",
            len(opportunities),
            len(top_opportunities),
            len(triggered_opportunities),
            elapsed,
        )
        return top_opportunities
