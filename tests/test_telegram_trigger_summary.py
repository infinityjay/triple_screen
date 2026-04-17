from __future__ import annotations

import unittest

from clients.telegram import TelegramNotifier
from config.schema import TelegramConfig


def _build_signal(direction: str) -> dict:
    return {
        "symbol": "PG" if direction == "SHORT" else "VRTX",
        "direction": direction,
        "execution_score": 9.4,
        "hourly": {"close": 146.73 if direction == "SHORT" else 440.55},
        "exits": {
            "entry": 146.10 if direction == "SHORT" else 438.20,
            "initial_stop_loss": 149.25 if direction == "SHORT" else 432.80,
            "reward_risk_ratio": 15.79 if direction == "SHORT" else 2.15,
        },
        "daily": {
            "elder_core_signal_count": 3,
            "elder_core_signal_total": 3,
        },
        "earnings": {"status": "CLEAR"},
    }


class TelegramTriggerSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.notifier = TelegramNotifier(TelegramConfig(enabled=False, bot_token=None, chat_id=None))

    def test_long_trigger_summary_includes_buy_price_and_initial_stop(self) -> None:
        message = self.notifier.format_trigger_summary_message(
            [_build_signal("LONG")],
            session_date="2026-04-10 ~ 2026-04-16",
            total_candidates=80,
            scan_time_sec=9.2,
        )

        self.assertIn("买入价 438.20", message)
        self.assertIn("初始止损 432.80", message)

    def test_short_trigger_summary_includes_sell_price_and_initial_stop(self) -> None:
        message = self.notifier.format_trigger_summary_message(
            [_build_signal("SHORT")],
            session_date="2026-04-10 ~ 2026-04-16",
            total_candidates=80,
            scan_time_sec=9.2,
        )

        self.assertIn("卖出价 146.10", message)
        self.assertIn("初始止损 149.25", message)


if __name__ == "__main__":
    unittest.main()
