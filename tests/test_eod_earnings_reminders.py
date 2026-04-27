from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from scan_engine import TripleScreenScanner
from clients.telegram import TelegramNotifier
from config.schema import TelegramConfig


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

    def test_candidate_summary_message_uses_weekly_and_daily_reasons(self) -> None:
        notifier = TelegramNotifier(TelegramConfig(enabled=False, bot_token=None, chat_id=None))

        message = notifier.format_candidate_summary_message(
            qualified_signals=[
                {
                    "symbol": "AAPL",
                    "direction": "LONG",
                    "opportunity_status": "MONITOR",
                    "candidate_score": 8.4,
                    "weekly": {"reason": "周线向上"},
                    "daily": {"reason": "日线结构完整", "rsi_state": "RECOVERING"},
                    "earnings": {},
                    "strong_divergence": False,
                }
            ],
            total_candidates=1,
            session_date="2026-04-09",
            scan_time_sec=0.5,
        )

        self.assertIn("周线：周线向上 · 日线：日线结构完整", message)
        self.assertNotIn("Force", message)

    def test_open_position_exit_alert_section_uses_model_risk_warning(self) -> None:
        notifier = TelegramNotifier(TelegramConfig(enabled=False, bot_token=None, chat_id=None))

        message = notifier.format_open_position_exit_alert_section(
            {
                "total_positions": 1,
                "alert_count": 1,
                "items": [
                    {
                        "symbol": "AAPL",
                        "direction": "long",
                        "weekly_impulse_color": "RED",
                        "daily_impulse_color": "RED",
                        "reason": "模型提示当前持仓面临大额亏损风险，按规则需要复核是否平仓。",
                    }
                ],
            }
        )

        self.assertIn("模型提示重大亏损风险", message)
        self.assertIn("需重点复核 1 笔", message)
        self.assertIn("模型提示当前持仓面临大额亏损风险", message)


if __name__ == "__main__":
    unittest.main()
