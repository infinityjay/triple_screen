from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests

from triple_screen.config.schema import PolygonConfig, UniverseConfig

logger = logging.getLogger(__name__)


class PolygonClient:
    def __init__(self, config: PolygonConfig) -> None:
        self.config = config

    def _get(self, endpoint: str, params: dict | None = None) -> dict | None:
        payload = dict(params or {})
        payload["apiKey"] = self.config.api_key

        for attempt in range(self.config.retry_attempts):
            try:
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
        endpoint = f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
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

    def get_weekly_bars(self, symbol: str) -> pd.DataFrame | None:
        end = datetime.utcnow().strftime("%Y-%m-%d")
        start = (datetime.utcnow() - timedelta(weeks=self.config.history.weekly_weeks)).strftime("%Y-%m-%d")
        return self.fetch_bars(symbol, "week", start, end)

    def get_daily_bars(self, symbol: str) -> pd.DataFrame | None:
        end = datetime.utcnow().strftime("%Y-%m-%d")
        start = (datetime.utcnow() - timedelta(days=self.config.history.daily_days)).strftime("%Y-%m-%d")
        return self.fetch_bars(symbol, "day", start, end)

    def get_hourly_bars(self, symbol: str) -> pd.DataFrame | None:
        end = datetime.utcnow().strftime("%Y-%m-%d")
        hours = self.config.history.hourly_hours + 48
        start = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d")
        return self.fetch_bars(symbol, "hour", start, end)
