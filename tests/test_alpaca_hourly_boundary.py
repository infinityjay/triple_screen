from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from alpaca import AlpacaClient
from schema import AlpacaCacheConfig, AlpacaConfig, AlpacaHistoryConfig, AlpacaRateLimitConfig


def _build_client() -> AlpacaClient:
    config = AlpacaConfig(
        api_key_id="test-key",
        api_secret_key="test-secret",
        market_data_base_url="https://example.com/v2",
        trading_base_url="https://example.com/v2",
        timeout_seconds=5,
        retry_attempts=1,
        retry_sleep_seconds=0,
        rate_limit_sleep_seconds=0,
        adjustment="split",
        feed="iex",
        history=AlpacaHistoryConfig(weekly_weeks=60, daily_days=90, hourly_hours=160),
        rate_limit=AlpacaRateLimitConfig(max_requests_per_minute=0),
        cache=AlpacaCacheConfig(enabled=True, overlap_bars=3),
    )
    return AlpacaClient(config, storage=None, market_timezone="America/New_York")


class AlpacaHourlyBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _build_client()
        self.market_tz = ZoneInfo("America/New_York")
        self.utc = ZoneInfo("UTC")

    def test_hourly_refresh_anchor_respects_0930_opening_bar(self) -> None:
        previous_close = datetime(2026, 4, 7, 16, 0, tzinfo=self.market_tz)
        opening_bar = datetime(2026, 4, 8, 9, 30, tzinfo=self.market_tz)
        next_bar = datetime(2026, 4, 8, 10, 30, tzinfo=self.market_tz)

        self.assertEqual(
            self.client._hourly_refresh_anchor(datetime(2026, 4, 8, 9, 29, tzinfo=self.market_tz)),
            previous_close,
        )
        self.assertEqual(
            self.client._hourly_refresh_anchor(datetime(2026, 4, 8, 9, 30, tzinfo=self.market_tz)),
            opening_bar,
        )
        self.assertEqual(
            self.client._hourly_refresh_anchor(datetime(2026, 4, 8, 10, 29, tzinfo=self.market_tz)),
            opening_bar,
        )
        self.assertEqual(
            self.client._hourly_refresh_anchor(datetime(2026, 4, 8, 10, 30, tzinfo=self.market_tz)),
            next_bar,
        )

    def test_hourly_cache_becomes_stale_as_soon_as_0930_bar_starts(self) -> None:
        last_sync_before_open = datetime(2026, 4, 8, 13, 29, tzinfo=self.utc)
        first_bar_live = datetime(2026, 4, 8, 13, 35, tzinfo=self.utc)

        self.assertTrue(self.client._is_cache_stale(last_sync_before_open, "hour", now=first_bar_live))

    def test_hourly_cache_rolls_to_next_bar_and_after_close(self) -> None:
        synced_during_opening_bar = datetime(2026, 4, 8, 13, 35, tzinfo=self.utc)
        before_next_bar = datetime(2026, 4, 8, 14, 29, tzinfo=self.utc)
        next_bar_open = datetime(2026, 4, 8, 14, 30, tzinfo=self.utc)
        synced_before_close = datetime(2026, 4, 8, 19, 45, tzinfo=self.utc)
        after_close = datetime(2026, 4, 8, 20, 10, tzinfo=self.utc)

        self.assertFalse(self.client._is_cache_stale(synced_during_opening_bar, "hour", now=before_next_bar))
        self.assertTrue(self.client._is_cache_stale(synced_during_opening_bar, "hour", now=next_bar_open))
        self.assertTrue(self.client._is_cache_stale(synced_before_close, "hour", now=after_close))


if __name__ == "__main__":
    unittest.main()
