from __future__ import annotations

import unittest

from clients.telegram import TelegramNotifier
from config.schema import TelegramConfig


def _build_signal(direction: str) -> dict:
    return {
        "symbol": "PG" if direction == "SHORT" else "VRTX",
        "direction": direction,
        "opportunity_status": "TRIGGERED",
        "execution_score": 9.4,
        "weekly": {
            "trend": direction,
            "reason": "weekly ok",
        },
        "hourly": {
            "close": 146.73 if direction == "SHORT" else 440.55,
            "current_high": 147.2 if direction == "SHORT" else 441.2,
            "current_low": 145.9 if direction == "SHORT" else 439.8,
            "signal_bar_high": None,
            "signal_bar_low": None,
            "entry_price": 146.10 if direction == "SHORT" else 438.20,
            "atr": 1.23,
            "status": "TRIGGERED",
            "trigger_source": "EMA_PENETRATION",
            "breakout_strength": None,
            "reason": "价格已触及参考价",
            "entry_plan": {},
        },
        "exits": {
            "entry": 146.10 if direction == "SHORT" else 438.20,
            "initial_stop_loss": 149.25 if direction == "SHORT" else 432.80,
            "initial_stop_safezone": 148.31 if direction == "SHORT" else 433.20,
            "initial_stop_nick": 149.70 if direction == "SHORT" else 431.10,
            "reward_risk_ratio": 15.79 if direction == "SHORT" else 2.15,
        },
        "daily": {
            "rsi_state": "PULLBACK_FORCE_BELOW_ZERO" if direction == "LONG" else "RALLY_FORCE_ABOVE_ZERO",
            "reason": "daily ok",
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
        self.assertIn("初始止损待你选择", message)

    def test_short_trigger_summary_includes_sell_price_and_initial_stop(self) -> None:
        message = self.notifier.format_trigger_summary_message(
            [_build_signal("SHORT")],
            session_date="2026-04-10 ~ 2026-04-16",
            total_candidates=80,
            scan_time_sec=9.2,
        )

        self.assertIn("卖出价 146.10", message)
        self.assertIn("初始止损待你选择", message)

    def test_detailed_signal_supports_ema_trigger_without_signal_bar(self) -> None:
        message = self.notifier.format_signal_message(_build_signal("SHORT"))

        self.assertIn("触发来源：EMA_PENETRATION", message)
        self.assertIn("触发价：146.10", message)

    def test_detailed_signal_escapes_strategy_text_for_telegram_html(self) -> None:
        signal = _build_signal("SHORT")
        signal["daily"]["reason"] = "最新高点 37.46 <= 防守位 38.00"
        signal["hourly"]["reason"] = "价格已触及卖空参考价（EMA_PENETRATION）"

        message = self.notifier.format_signal_message(signal)

        self.assertIn("37.46 &lt;= 防守位", message)
        self.assertNotIn("37.46 <=", message)


if __name__ == "__main__":
    unittest.main()
