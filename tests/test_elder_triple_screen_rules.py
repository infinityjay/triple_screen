from __future__ import annotations

import unittest

import pandas as pd

import indicators
from config.schema import DailyStrategyConfig, HourlyStrategyConfig, StrategyConfig, WeeklyStrategyConfig


def _settings() -> StrategyConfig:
    return StrategyConfig(
        weekly=WeeklyStrategyConfig(
            macd_fast=12,
            macd_slow=26,
            macd_signal=9,
            confirm_bars=1,
            require_impulse_alignment=True,
        ),
        daily=DailyStrategyConfig(
            rsi_period=14,
            rsi_oversold=35,
            rsi_overbought=65,
            recovery_mode=True,
            value_band_atr_multiplier=0.75,
        ),
        hourly=HourlyStrategyConfig(trigger_mode="trailing_bar", atr_period=14),
    )


def _frame(closes: list[float], volume: float = 1_000_000) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "open": [value * 0.99 for value in closes],
            "high": [value * 1.02 for value in closes],
            "low": [value * 0.98 for value in closes],
            "close": closes,
            "volume": [volume] * len(closes),
        },
        index=index,
    )


class ElderTripleScreenRuleTests(unittest.TestCase):
    def test_weekly_impulse_color_is_monitoring_gate(self) -> None:
        closes = [100 + index * 0.4 for index in range(70)]
        result = indicators.screen_weekly(_frame(closes), _settings())

        self.assertEqual(result["impulse_color"], "GREEN")
        self.assertTrue(result["allows_long"])
        self.assertFalse(result["allows_short"])

    def test_daily_force_index_below_zero_qualifies_long_pullback(self) -> None:
        closes = [100 + index * 0.45 for index in range(35)] + [116, 115, 114]
        frame = _frame(closes)
        result = indicators.screen_daily(frame, "LONG", _settings())

        self.assertLess(result["force_index_ema2"], 0)
        self.assertIn(result["state"], {"QUALIFIED", "WATCH"})
        self.assertTrue(result["force_signal"])
        self.assertTrue(result["entry_plan"]["available"])

    def test_nick_stop_exposes_reference_date(self) -> None:
        index = pd.date_range("2026-04-01", periods=8, freq="D")
        frame = pd.DataFrame(
            {
                "open": [100, 102, 101, 104, 103, 106, 105, 107],
                "high": [101, 103, 102, 105, 104, 107, 106, 108],
                "low": [99, 100, 98, 101, 97, 103, 102, 104],
                "close": [100, 102, 101, 104, 103, 106, 105, 107],
                "volume": [1_000_000] * 8,
            },
            index=index,
        )

        detail = indicators.calc_nick_stop_detail(frame, "LONG")

        self.assertIsNotNone(detail)
        self.assertEqual(detail["reference_date"], "2026-04-03")
        self.assertEqual(round(detail["stop"], 2), 97.99)


if __name__ == "__main__":
    unittest.main()
