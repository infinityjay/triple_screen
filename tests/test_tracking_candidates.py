from __future__ import annotations

import unittest
from datetime import datetime, time
from zoneinfo import ZoneInfo

from scan_engine import HISTORY_SESSION_LIMIT, TripleScreenScanner, TRACKING_SESSION_LIMIT


class _FakeCandidateStorage:
    def __init__(self) -> None:
        self.session_limits: list[int] = []

    def get_recent_qualified_candidates(self, session_limit: int = 5) -> list[dict]:
        self.session_limits.append(session_limit)
        latest = [
            {
                "symbol": "AAPL",
                "direction": "LONG",
                "opportunity_status": "WATCHLIST",
                "candidate_score": 8.0,
                "priority_tags": [],
                "source_session_date": "2026-04-30",
                "stored_session_date": "2026-04-30",
            }
        ]
        if session_limit == TRACKING_SESSION_LIMIT:
            return latest
        return [
            *latest,
            {
                "symbol": "AAPL",
                "direction": "LONG",
                "opportunity_status": "WATCHLIST",
                "candidate_score": 7.8,
                "priority_tags": [],
                "source_session_date": "2026-04-29",
                "stored_session_date": "2026-04-29",
            },
            {
                "symbol": "TSLA",
                "direction": "SHORT",
                "opportunity_status": "WATCHLIST",
                "candidate_score": 7.5,
                "priority_tags": [],
                "source_session_date": "2026-04-29",
                "stored_session_date": "2026-04-29",
            },
        ]


class TrackingCandidateTests(unittest.TestCase):
    def test_intraday_tracking_uses_only_latest_candidate_session(self) -> None:
        scanner = TripleScreenScanner.__new__(TripleScreenScanner)
        storage = _FakeCandidateStorage()
        scanner.storage = storage

        candidates, label = scanner._load_tracking_candidates()

        self.assertEqual(TRACKING_SESSION_LIMIT, 1)
        self.assertEqual(storage.session_limits, [TRACKING_SESSION_LIMIT, HISTORY_SESSION_LIMIT])
        self.assertEqual(label, "2026-04-30")
        self.assertEqual([candidate["symbol"] for candidate in candidates], ["AAPL"])
        self.assertEqual(candidates[0]["history"]["appearance_count"], 2)
        self.assertEqual(candidates[0]["history"]["consecutive_sessions"], 2)
        self.assertIn("连续2次入选", candidates[0]["priority_tags"])
        self.assertGreater(candidates[0]["candidate_rank_score"], candidates[0]["candidate_score"])

    def test_history_enhancement_keeps_old_symbols_out_of_latest_candidates(self) -> None:
        scanner = TripleScreenScanner.__new__(TripleScreenScanner)
        storage = _FakeCandidateStorage()
        scanner.storage = storage

        candidates = [
            {
                "symbol": "AAPL",
                "direction": "LONG",
                "opportunity_status": "WATCHLIST",
                "candidate_score": 8.0,
                "priority_tags": [],
            }
        ]

        scanner._apply_history_enhancement(candidates, "2026-04-30", storage.get_recent_qualified_candidates(HISTORY_SESSION_LIMIT))

        self.assertEqual([candidate["symbol"] for candidate in candidates], ["AAPL"])
        self.assertEqual(candidates[0]["history"]["prior_appearance_count"], 1)

    def test_auto_mode_only_runs_eod_during_close_window_and_skips_afterward(self) -> None:
        scanner = TripleScreenScanner.__new__(TripleScreenScanner)
        scanner.market_timezone = ZoneInfo("America/New_York")
        scanner.market_open_time = time(hour=9, minute=30)
        scanner.market_close_time = time(hour=16, minute=0)
        scanner.eod_auto_cutoff_time = time(hour=16, minute=45)
        calls: list[str] = []

        scanner.run_end_of_day_scan = lambda: calls.append("eod") or []
        scanner.run_intraday_scan = lambda: calls.append("intraday") or []
        scanner._market_now = lambda: datetime(2026, 4, 30, 16, 10, tzinfo=ZoneInfo("America/New_York"))

        scanner.run_scan("auto")
        self.assertEqual(calls, ["eod"])

        scanner._market_now = lambda: datetime(2026, 4, 30, 17, 30, tzinfo=ZoneInfo("America/New_York"))

        result = scanner.run_scan("auto")
        self.assertEqual(result, [])
        self.assertEqual(calls, ["eod"])


if __name__ == "__main__":
    unittest.main()
