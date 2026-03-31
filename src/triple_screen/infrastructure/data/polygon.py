from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests

from triple_screen.config.schema import PolygonConfig, UniverseConfig
from triple_screen.infrastructure.storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)


class SlidingWindowRateLimiter:
    def __init__(self, max_requests_per_minute: int) -> None:
        self.max_requests_per_minute = max_requests_per_minute
        self.window_seconds = 60.0
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.max_requests_per_minute <= 0:
            return

        while True:
            wait_seconds = 0.0
            with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self.window_seconds:
                    self._timestamps.popleft()

                if len(self._timestamps) < self.max_requests_per_minute:
                    self._timestamps.append(now)
                    return

                wait_seconds = self.window_seconds - (now - self._timestamps[0]) + 0.05

            logger.info("Polygon proactive throttle sleeping %.1fs", wait_seconds)
            time.sleep(max(wait_seconds, 0.05))


class PolygonClient:
    def __init__(self, config: PolygonConfig, storage: SQLiteStorage | None = None) -> None:
        self.config = config
        self.storage = storage
        self.rate_limiter = SlidingWindowRateLimiter(config.rate_limit.max_requests_per_minute)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.replace("-", ".") if "-" in symbol else symbol

    def _timeframe_metadata(self, timeframe: str) -> dict:
        if timeframe == "week":
            return {
                "timespan": "week",
                "history_delta": timedelta(weeks=self.config.history.weekly_weeks),
                "keep_rows": self.config.history.weekly_weeks + self.config.cache.overlap_bars + 8,
            }
        if timeframe == "day":
            return {
                "timespan": "day",
                "history_delta": timedelta(days=self.config.history.daily_days),
                "keep_rows": self.config.history.daily_days + self.config.cache.overlap_bars + 10,
            }
        if timeframe == "hour":
            return {
                "timespan": "hour",
                "history_delta": timedelta(hours=self.config.history.hourly_hours + 48),
                "keep_rows": self.config.history.hourly_hours + self.config.cache.overlap_bars + 12,
            }
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    @staticmethod
    def _is_cache_stale(last_sync_time: datetime | None, timeframe: str) -> bool:
        if last_sync_time is None:
            return True

        now = datetime.utcnow()
        if timeframe == "hour":
            current_bucket = now.replace(minute=0, second=0, microsecond=0)
            last_bucket = last_sync_time.replace(minute=0, second=0, microsecond=0)
            return current_bucket > last_bucket
        if timeframe == "day":
            return now.date() > last_sync_time.date()
        if timeframe == "week":
            return now.isocalendar()[:2] != last_sync_time.isocalendar()[:2]
        return True

    def _bootstrap_bars(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        metadata = self._timeframe_metadata(timeframe)
        end = datetime.utcnow().strftime("%Y-%m-%d")
        start = (datetime.utcnow() - metadata["history_delta"]).strftime("%Y-%m-%d")
        frame = self.fetch_bars(symbol, metadata["timespan"], start, end)
        if frame is not None and self.storage and self.config.cache.enabled:
            self.storage.upsert_price_bars(symbol, timeframe, frame)
            self.storage.trim_price_bars(symbol, timeframe, metadata["keep_rows"])
            return self.storage.get_price_bars(symbol, timeframe)
        return frame

    def _refresh_cached_bars(self, symbol: str, timeframe: str, cached: pd.DataFrame) -> pd.DataFrame:
        metadata = self._timeframe_metadata(timeframe)
        overlap = min(self.config.cache.overlap_bars, len(cached))
        refresh_start = cached.index[-overlap] if overlap > 0 else cached.index[-1]
        frame = self.fetch_bars(
            symbol,
            metadata["timespan"],
            pd.Timestamp(refresh_start).strftime("%Y-%m-%d"),
            datetime.utcnow().strftime("%Y-%m-%d"),
        )
        if frame is not None and self.storage:
            self.storage.upsert_price_bars(symbol, timeframe, frame)
            self.storage.trim_price_bars(symbol, timeframe, metadata["keep_rows"])
            refreshed = self.storage.get_price_bars(symbol, timeframe)
            if refreshed is not None:
                return refreshed
        return cached

    def _get(self, endpoint: str, params: dict | None = None) -> dict | None:
        payload = dict(params or {})
        payload["apiKey"] = self.config.api_key

        for attempt in range(self.config.retry_attempts):
            try:
                self.rate_limiter.acquire()
                response = requests.get(
                    f"{self.config.base_url}{endpoint}",
                    params=payload,
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code == 429:
                    logger.warning("Polygon rate limited, sleeping before retry.")
                    time.sleep(self.config.rate_limit_sleep_seconds)
                    continue
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                logger.warning("Polygon request failed (%s/%s): %s", attempt + 1, self.config.retry_attempts, exc)
                time.sleep(self.config.retry_sleep_seconds)
        return None

    def get_top_symbols(self, universe: UniverseConfig) -> list[dict]:
        if universe.mode == "static_file" and universe.symbols:
            return [
                {
                    "symbol": item.get("ticker") or item.get("symbol"),
                    "name": item.get("name"),
                    "market_cap": item.get("market_cap"),
                    "sector": item.get("sector") or item.get("country"),
                    "rank": item.get("rank"),
                }
                for item in universe.symbols[: universe.top_n]
                if item.get("ticker") or item.get("symbol")
            ]

        if universe.mode == "custom":
            return [{"symbol": symbol, "market_cap": None, "sector": "CUSTOM"} for symbol in universe.custom_symbols]

        results: list[dict] = []
        cursor: str | None = None
        excluded = tuple(universe.exclude_symbols_containing)

        while len(results) < universe.top_n:
            params = {
                "market": "stocks",
                "locale": "us",
                "active": "true",
                "sort": "market_cap",
                "order": "desc",
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor

            payload = self._get("/v3/reference/tickers", params)
            if not payload or "results" not in payload:
                break

            for item in payload["results"]:
                symbol = item.get("ticker", "")
                if item.get("type") not in universe.allowed_ticker_types:
                    continue
                if excluded and any(fragment in symbol for fragment in excluded):
                    continue

                results.append(
                    {
                        "symbol": symbol,
                        "market_cap": item.get("market_cap"),
                        "sector": item.get("sic_description") or item.get("primary_exchange"),
                    }
                )
                if len(results) >= universe.top_n:
                    break

            next_url = payload.get("next_url")
            if not next_url:
                break
            cursor_values = parse_qs(urlparse(next_url).query).get("cursor")
            cursor = cursor_values[0] if cursor_values else None
            if not cursor:
                break

        logger.info("Loaded %s symbols from Polygon universe endpoint.", len(results))
        return results[: universe.top_n]

    def fetch_bars(self, symbol: str, timespan: str, from_date: str, to_date: str, multiplier: int = 1) -> pd.DataFrame | None:
        api_symbol = self._normalize_symbol(symbol)
        endpoint = f"/v2/aggs/ticker/{api_symbol}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        payload = self._get(
            endpoint,
            {
                "adjusted": str(self.config.adjusted).lower(),
                "sort": "asc",
                "limit": 500,
            },
        )

        if not payload or payload.get("resultsCount", 0) == 0:
            return None

        frame = pd.DataFrame(payload["results"])
        frame.rename(
            columns={
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
                "t": "timestamp",
            },
            inplace=True,
        )
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms")
        frame.set_index("timestamp", inplace=True)
        return frame

    def _get_cached_or_incremental_bars(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        if not self.storage or not self.config.cache.enabled:
            metadata = self._timeframe_metadata(timeframe)
            end = datetime.utcnow().strftime("%Y-%m-%d")
            start = (datetime.utcnow() - metadata["history_delta"]).strftime("%Y-%m-%d")
            return self.fetch_bars(symbol, metadata["timespan"], start, end)

        cached = self.storage.get_price_bars(symbol, timeframe)
        if cached is None or cached.empty:
            logger.info("[%s/%s] cache miss, bootstrapping from Polygon", symbol, timeframe)
            return self._bootstrap_bars(symbol, timeframe)

        last_sync_time = self.storage.get_latest_bar_sync_time(symbol, timeframe)
        if self._is_cache_stale(last_sync_time, timeframe):
            logger.info("[%s/%s] cache stale, fetching incremental bars", symbol, timeframe)
            return self._refresh_cached_bars(symbol, timeframe, cached)

        logger.debug("[%s/%s] cache hit", symbol, timeframe)
        return cached

    def get_weekly_bars(self, symbol: str) -> pd.DataFrame | None:
        return self._get_cached_or_incremental_bars(symbol, "week")

    def get_daily_bars(self, symbol: str) -> pd.DataFrame | None:
        return self._get_cached_or_incremental_bars(symbol, "day")

    def get_hourly_bars(self, symbol: str) -> pd.DataFrame | None:
        return self._get_cached_or_incremental_bars(symbol, "hour")
