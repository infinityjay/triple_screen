from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from clients.earnings import EarningsCalendarClient


class _FakeEarningsStorage:
    def get_earnings_event(self, symbol: str):
        return {
            "symbol": symbol,
            "report_date": "2026-04-22",
            "fiscal_date_ending": None,
            "estimate": None,
            "updated_at": "2026-05-01T10:00:00+00:00",
        }

    def get_latest_earnings_update_time(self):
        return datetime.now(UTC)


class EarningsCalendarTests(unittest.TestCase):
    def test_recent_cache_does_not_return_stale_report_dates(self) -> None:
        client = EarningsCalendarClient(
            SimpleNamespace(enabled=True, provider="alphavantage", api_key=None),
            storage=_FakeEarningsStorage(),
        )

        events = client.get_upcoming_earnings(["AAPL"], session_date=datetime(2026, 5, 1).date())

        self.assertEqual(events, {})


if __name__ == "__main__":
    unittest.main()
