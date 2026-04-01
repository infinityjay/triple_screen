from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import date, datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from triple_screen.config.schema import AlpacaConfig, UniverseConfig
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

            logger.info("Alpaca proactive throttle sleeping %.1fs", wait_seconds)
            time.sleep(max(wait_seconds, 0.05))


class AlpacaClient:
    def __init__(
        self,
        config: AlpacaConfig,
        storage: SQLiteStorage | None = None,
        market_timezone: str = "America/New_York",
    ) -> None:
        self.config = config
        self.storage = storage
        self.market_timezone = ZoneInfo(market_timezone)
        self.market_close_time = clock_time(hour=16, minute=0)
        self.rate_limiter = SlidingWindowRateLimiter(config.rate_limit.max_requests_per_minute)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.replace("-", ".")

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        return base_url.rstrip("/")

    @classmethod
    def _build_url(cls, base_url: str, endpoint: str) -> str:
        normalized_base = cls._normalize_base_url(base_url)
        normalized_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"

        if normalized_base.endswith("/v2") and normalized_endpoint.startswith("/v2/"):
            normalized_endpoint = normalized_endpoint[3:]

        return f"{normalized_base}{normalized_endpoint}"

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.config.api_key_id,
            "APCA-API-SECRET-KEY": self.config.api_secret_key,
        }

    @staticmethod
    def _to_rfc3339(timestamp: datetime | pd.Timestamp) -> str:
        value = pd.Timestamp(timestamp)
        if value.tzinfo is None:
            value = value.tz_localize("UTC")
        else:
            value = value.tz_convert("UTC")
        return value.isoformat().replace("+00:00", "Z")

    def _timeframe_metadata(self, timeframe: str) -> dict:
        if timeframe == "week":
            return {
                "api_timeframe": "1Week",
                "history_delta": timedelta(weeks=self.config.history.weekly_weeks),
                "keep_rows": self.config.history.weekly_weeks + self.config.cache.overlap_bars + 8,
            }
        if timeframe == "day":
            return {
                "api_timeframe": "1Day",
                "history_delta": timedelta(days=self.config.history.daily_days),
                "keep_rows": self.config.history.daily_days + self.config.cache.overlap_bars + 10,
            }
        if timeframe == "hour":
            return {
                "api_timeframe": "1Hour",
                "history_delta": timedelta(hours=self.config.history.hourly_hours + 48),
                "keep_rows": self.config.history.hourly_hours + self.config.cache.overlap_bars + 12,
            }
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    def _to_market_datetime(self, timestamp: datetime) -> datetime:
        value = pd.Timestamp(timestamp)
        if value.tzinfo is None:
            value = value.tz_localize("UTC")
        else:
            value = value.tz_convert("UTC")
        return value.tz_convert(self.market_timezone).to_pydatetime()

    def _market_close_at(self, session_date: date) -> datetime:
        return datetime.combine(session_date, self.market_close_time, tzinfo=self.market_timezone)

    @staticmethod
    def _previous_weekday(session_date: date) -> date:
        candidate = session_date - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate

    def _latest_completed_market_close(self, now_local: datetime) -> datetime:
        today_close = self._market_close_at(now_local.date())
        if now_local.weekday() < 5 and now_local >= today_close:
            return today_close
        return self._market_close_at(self._previous_weekday(now_local.date()))

    def _is_cache_stale(self, last_sync_time: datetime | None, timeframe: str) -> bool:
        if last_sync_time is None:
            return True

        now = datetime.utcnow()
        if timeframe == "hour":
            current_bucket = now.replace(minute=0, second=0, microsecond=0)
            last_bucket = last_sync_time.replace(minute=0, second=0, microsecond=0)
            return current_bucket > last_bucket
        if timeframe in {"day", "week"}:
            now_local = self._to_market_datetime(now)
            last_sync_local = self._to_market_datetime(last_sync_time)
            return last_sync_local < self._latest_completed_market_close(now_local)
        return True

    def _bootstrap_bars(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        metadata = self._timeframe_metadata(timeframe)
        end = datetime.utcnow()
        start = end - metadata["history_delta"]
        frame = self.fetch_bars(symbol, metadata["api_timeframe"], start, end)
        if frame is not None and self.storage and self.config.cache.enabled:
            self.storage.upsert_price_bars(symbol, timeframe, frame)
            self.storage.trim_price_bars(symbol, timeframe, metadata["keep_rows"])
            return self.storage.get_price_bars(symbol, timeframe)
        return frame

    def _bootstrap_bars_batch(self, symbols: list[str], timeframe: str) -> None:
        if not symbols:
            return

        metadata = self._timeframe_metadata(timeframe)
        end = datetime.utcnow()
        start = end - metadata["history_delta"]
        frames = self.fetch_bars_batch(symbols, metadata["api_timeframe"], start, end)
        self._persist_batch_frames(frames, timeframe, metadata["keep_rows"])

    def _refresh_cached_bars(self, symbol: str, timeframe: str, cached: pd.DataFrame) -> pd.DataFrame:
        metadata = self._timeframe_metadata(timeframe)
        overlap = min(self.config.cache.overlap_bars, len(cached))
        refresh_start = cached.index[-overlap] if overlap > 0 else cached.index[-1]
        frame = self.fetch_bars(symbol, metadata["api_timeframe"], pd.Timestamp(refresh_start).to_pydatetime(), datetime.utcnow())
        if frame is not None and self.storage:
            self.storage.upsert_price_bars(symbol, timeframe, frame)
            self.storage.trim_price_bars(symbol, timeframe, metadata["keep_rows"])
            refreshed = self.storage.get_price_bars(symbol, timeframe)
            if refreshed is not None:
                return refreshed
        return cached

    def _refresh_cached_bars_batch(self, symbols: list[str], timeframe: str) -> None:
        if not symbols or not self.storage:
            return

        metadata = self._timeframe_metadata(timeframe)
        refresh_starts: list[datetime] = []
        for symbol in symbols:
            cached = self.storage.get_price_bars(symbol, timeframe)
            if cached is None or cached.empty:
                continue
            overlap = min(self.config.cache.overlap_bars, len(cached))
            refresh_start = cached.index[-overlap] if overlap > 0 else cached.index[-1]
            refresh_starts.append(pd.Timestamp(refresh_start).to_pydatetime())

        if not refresh_starts:
            return

        frames = self.fetch_bars_batch(symbols, metadata["api_timeframe"], min(refresh_starts), datetime.utcnow())
        self._persist_batch_frames(frames, timeframe, metadata["keep_rows"])

    def _persist_batch_frames(self, frames: dict[str, pd.DataFrame], timeframe: str, keep_rows: int) -> None:
        if not self.storage:
            return

        for symbol, frame in frames.items():
            if frame is None or frame.empty:
                continue
            self.storage.upsert_price_bars(symbol, timeframe, frame)
            self.storage.trim_price_bars(symbol, timeframe, keep_rows)

    def _request_json(self, base_url: str, endpoint: str, params: dict | None = None) -> dict | list | None:
        for attempt in range(self.config.retry_attempts):
            try:
                self.rate_limiter.acquire()
                response = requests.get(
                    self._build_url(base_url, endpoint),
                    params=params,
                    headers=self._headers(),
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code == 429:
                    logger.warning("Alpaca rate limited, sleeping before retry.")
                    time.sleep(self.config.rate_limit_sleep_seconds)
                    continue
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                logger.warning("Alpaca request failed (%s/%s): %s", attempt + 1, self.config.retry_attempts, exc)
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

        logger.warning(
            "Universe mode '%s' is being approximated with Alpaca active assets because Alpaca does not expose market-cap ranking.",
            universe.mode,
        )
        payload = self._request_json(
            self.config.trading_base_url,
            "/v2/assets",
            {
                "status": "active",
                "asset_class": "us_equity",
            },
        )
        if not isinstance(payload, list):
            return []

        excluded = tuple(universe.exclude_symbols_containing)
        results: list[dict] = []
        for item in payload:
            symbol = item.get("symbol", "")
            if not item.get("tradable", False):
                continue
            if item.get("exchange") == "OTC":
                continue
            if excluded and any(fragment in symbol for fragment in excluded):
                continue
            results.append(
                {
                    "symbol": symbol,
                    "name": item.get("name"),
                    "market_cap": None,
                    "sector": item.get("exchange"),
                }
            )

        results.sort(key=lambda item: item["symbol"])
        logger.info(
            "Loaded %s symbols from Alpaca assets endpoint (market_cap not provided by Alpaca).",
            len(results),
        )
        return results[: universe.top_n]

    def fetch_bars_batch(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, pd.DataFrame]:
        if not symbols:
            return {}

        symbol_map = {self._normalize_symbol(symbol): symbol for symbol in symbols}
        endpoint = "/v2/stocks/bars"
        params = {
            "symbols": ",".join(symbol_map.keys()),
            "timeframe": timeframe,
            "start": self._to_rfc3339(start),
            "end": self._to_rfc3339(end),
            "adjustment": self.config.adjustment,
            "feed": self.config.feed,
            "sort": "asc",
            "limit": 10000,
        }

        aggregated_bars: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
        page_token: str | None = None
        while True:
            request_params = dict(params)
            if page_token:
                request_params["page_token"] = page_token

            payload = self._request_json(self.config.market_data_base_url, endpoint, request_params)
            if not isinstance(payload, dict):
                return {}

            for response_symbol, response_bars in payload.get("bars", {}).items():
                original_symbol = symbol_map.get(response_symbol, response_symbol)
                aggregated_bars.setdefault(original_symbol, []).extend(response_bars)

            page_token = payload.get("next_page_token")
            if not page_token:
                break

        frames: dict[str, pd.DataFrame] = {}
        for symbol, bars in aggregated_bars.items():
            if not bars:
                continue
            frame = pd.DataFrame(bars)
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
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(None)
            frame.set_index("timestamp", inplace=True)
            frames[symbol] = frame[["open", "high", "low", "close", "volume"]]

        logger.info(
            "Fetched %s timeframe bars in batch for %s symbols from %s to %s.",
            timeframe,
            len(symbols),
            self._to_rfc3339(start),
            self._to_rfc3339(end),
        )
        return frames

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame | None:
        api_symbol = self._normalize_symbol(symbol)
        endpoint = f"/v2/stocks/{api_symbol}/bars"
        params = {
            "timeframe": timeframe,
            "start": self._to_rfc3339(start),
            "end": self._to_rfc3339(end),
            "adjustment": self.config.adjustment,
            "feed": self.config.feed,
            "sort": "asc",
            "limit": 10000,
        }

        bars: list[dict] = []
        page_token: str | None = None
        while True:
            request_params = dict(params)
            if page_token:
                request_params["page_token"] = page_token

            payload = self._request_json(self.config.market_data_base_url, endpoint, request_params)
            if not isinstance(payload, dict):
                return None

            bars.extend(payload.get("bars", []))
            page_token = payload.get("next_page_token")
            if not page_token:
                break

        if not bars:
            return None

        frame = pd.DataFrame(bars)
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
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(None)
        frame.set_index("timestamp", inplace=True)
        return frame[["open", "high", "low", "close", "volume"]]

    def warm_cache_for_scan(self, symbols: list[str], benchmark_symbol: str | None = None) -> None:
        if not self.storage or not self.config.cache.enabled:
            return

        weekly_symbols = list(symbols)
        if benchmark_symbol and benchmark_symbol not in weekly_symbols:
            weekly_symbols.append(benchmark_symbol)

        self._warm_cache_for_timeframe(weekly_symbols, "week")
        self._warm_cache_for_timeframe(symbols, "day")
        self._warm_cache_for_timeframe(symbols, "hour")

    def _warm_cache_for_timeframe(self, symbols: list[str], timeframe: str) -> None:
        if not symbols or not self.storage:
            return

        missing_symbols: list[str] = []
        stale_symbols: list[str] = []

        for symbol in symbols:
            cached = self.storage.get_price_bars(symbol, timeframe)
            if cached is None or cached.empty:
                missing_symbols.append(symbol)
                continue

            last_sync_time = self.storage.get_latest_bar_sync_time(symbol, timeframe)
            if self._is_cache_stale(last_sync_time, timeframe):
                stale_symbols.append(symbol)

        if missing_symbols:
            logger.info("[%s] bootstrapping batch cache for %s symbols", timeframe, len(missing_symbols))
            self._bootstrap_bars_batch(missing_symbols, timeframe)

        if stale_symbols:
            logger.info("[%s] refreshing batch cache for %s symbols", timeframe, len(stale_symbols))
            self._refresh_cached_bars_batch(stale_symbols, timeframe)

    def _get_cached_or_incremental_bars(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        if not self.storage or not self.config.cache.enabled:
            metadata = self._timeframe_metadata(timeframe)
            end = datetime.utcnow()
            start = end - metadata["history_delta"]
            return self.fetch_bars(symbol, metadata["api_timeframe"], start, end)

        cached = self.storage.get_price_bars(symbol, timeframe)
        if cached is None or cached.empty:
            logger.info("[%s/%s] cache miss, bootstrapping from Alpaca", symbol, timeframe)
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
