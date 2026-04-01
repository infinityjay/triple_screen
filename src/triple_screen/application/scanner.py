from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from triple_screen.config.schema import AppConfig
from triple_screen.infrastructure.data.alpaca import AlpacaClient
from triple_screen.infrastructure.notifications.telegram import TelegramNotifier
from triple_screen.infrastructure.storage.sqlite import SQLiteStorage
from triple_screen.strategy import indicators

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

    def _process_symbol(self, symbol: str, market_trend: str) -> dict | None:
        try:
            weekly_frame = self.market_data.get_weekly_bars(symbol)
            weekly = indicators.screen_weekly(weekly_frame, self.settings.strategy)
            if not weekly["pass"]:
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
            if not hourly["pass"]:
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

            if not self.dry_run and self._is_recently_alerted(symbol, trend):
                logger.info("[%s] skipped because alert cooldown is active.", symbol)
                return None

            if hourly_frame is not None and len(hourly_frame) >= 2:
                prev_low = float(hourly_frame["low"].iloc[-2])
                prev_high = float(hourly_frame["high"].iloc[-2])
            else:
                prev_low = None
                prev_high = None

            exits = indicators.calc_exits(
                trend,
                hourly["close"],
                hourly["atr"],
                self.settings.risk,
                prev_candle_low=prev_low,
                prev_candle_high=prev_high,
            )
            score = indicators.calc_signal_score(weekly, daily, hourly)

            signal = {
                "symbol": symbol,
                "direction": trend,
                "signal_score": score,
                "weekly": weekly,
                "daily": daily,
                "hourly": hourly,
                "exits": exits,
            }

            self.storage.save_signal(
                symbol,
                trend,
                hourly["close"],
                exits["sl_atr"],
                exits["sl_prev_candle"],
                exits["tp_fixed_rr"],
                score,
                weekly["histogram"],
                weekly["trend"],
                daily["rsi"],
                hourly["close"],
                hourly["atr"],
                exits["position_size"],
            )

            logger.info("[%s] signal found: %s (score=%.2f)", symbol, trend, score)
            return signal
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
        if not self.dry_run:
            self.notifier.send_scan_start(len(symbols))

        benchmark_symbol = self.settings.market_filter.benchmark_symbol if self.settings.market_filter.enabled else None
        self.market_data.warm_cache_for_scan(symbols, benchmark_symbol=benchmark_symbol)

        market_trend = self._check_market_trend()
        logger.info("market trend: %s", market_trend)

        signals: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.settings.runtime.max_workers) as executor:
            futures = {executor.submit(self._process_symbol, symbol, market_trend): symbol for symbol in symbols}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    signals.append(result)

        signals.sort(key=lambda item: item["signal_score"], reverse=True)
        top_signals = signals[: self.settings.alerts.max_signals_per_scan]

        if self.dry_run:
            logger.info("dry-run would emit %s notifications", len(top_signals))
        else:
            for signal in top_signals:
                self.notifier.send_signal(signal)
                self.storage.update_alert_log(signal["symbol"], signal["direction"])
                time.sleep(1)

        elapsed = time.time() - started_at
        if not self.dry_run:
            self.notifier.send_summary(top_signals, elapsed)
        logger.info(
            "scan finished: %s total signals, %s pushed, elapsed %.1fs",
            len(signals),
            len(top_signals),
            elapsed,
        )
        return top_signals
