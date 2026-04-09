from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from scan_engine import TripleScreenScanner
from schema import TelegramConfig
from telegram import TelegramNotifier


class _FakeStorage:
    def list_open_trades(self) -> list[dict]:
        return [
            {"id": "1", "stock": "AAPL", "direction": "long"},
            {"id": "2", "stock": "MSFT", "direction": "short"},
            {"id": "3", "stock": "NVDA", "direction": "long"},
        ]


class _FakeEarningsCalendar:
    def get_upcoming_earnings(self, symbols: list[str], session_date: date | None = None) -> dict[str, dict]:
        return {
            "AAPL": {"report_date": "2026-04-10"},
            "MSFT": {"report_date": "2026-04-11"},
            "NVDA": {"report_date": "2026-04-15"},
        }


class EodEarningsReminderTests(unittest.TestCase):
    def test_open_position_earnings_summary_only_keeps_nearby_reports(self) -> None:
        scanner = TripleScreenScanner.__new__(TripleScreenScanner)
        scanner.settings = SimpleNamespace(
            qualification=SimpleNamespace(
                earnings_block_days_before=2,
                earnings_block_days_after=1,
                earnings_warn_days_before=5,
            )
        )
        scanner.storage = _FakeStorage()
        scanner.earnings_calendar = _FakeEarningsCalendar()

        summary = scanner._build_open_position_earnings_summary(date(2026, 4, 9))

        self.assertEqual(summary["total_positions"], 3)
        self.assertEqual(summary["reminder_count"], 2)
        self.assertEqual([item["symbol"] for item in summary["items"]], ["AAPL", "MSFT"])
        self.assertEqual(summary["items"][0]["days_until"], 1)
        self.assertEqual(summary["items"][1]["days_until"], 2)

    def test_candidate_summary_message_includes_open_position_earnings_section(self) -> None:
        notifier = TelegramNotifier(TelegramConfig(enabled=False, bot_token=None, chat_id=None))

        message = notifier.format_candidate_summary_message(
            qualified_signals=[],
            total_candidates=0,
            session_date="2026-04-09",
            scan_time_sec=1.2,
            open_position_earnings_summary={
                "total_positions": 2,
                "reminder_count": 1,
                "window_days": 2,
                "items": [
                    {
                        "symbol": "AAPL",
                        "direction": "long",
                        "report_date": "2026-04-10",
                        "days_until": 1,
                    }
                ],
            },
        )

        self.assertIn("持仓临近财报提醒", message)
        self.assertIn("AAPL", message)
        self.assertIn("提前卖出或减仓", message)


if __name__ == "__main__":
    unittest.main()
