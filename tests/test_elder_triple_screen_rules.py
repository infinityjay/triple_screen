from __future__ import annotations

import unittest

import pandas as pd

import indicators
from config.schema import DailyStrategyConfig, HourlyStrategyConfig, StrategyConfig, TradePlanConfig, WeeklyStrategyConfig
import trading_models


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

    def test_elder_force_model_hourly_plan_exposes_storage_breakout_flags(self) -> None:
        index = pd.date_range("2026-04-01 13:00", periods=20, freq="h")
        hourly = pd.DataFrame(
            {
                "open": [100.0] * 20,
                "high": [101.0] * 20,
                "low": [99.0] * 20,
                "close": [100.0] * 20,
                "volume": [1_000_000] * 20,
            },
            index=index,
        )
        daily = _frame([100 + idx for idx in range(40)])
        daily.iloc[-1, daily.columns.get_loc("high")] = 120.0
        daily.iloc[-1, daily.columns.get_loc("low")] = 95.0
        daily.iloc[-1, daily.columns.get_loc("close")] = 110.0
        daily.iloc[-1, daily.columns.get_loc("volume")] = 1_000_000

        plan = trading_models.get_model("elder_force").build_intraday_plan(
            direction="LONG",
            daily_frame=daily,
            weekly_frame=daily,
            hourly_frame=hourly,
            settings=_settings(),
            trade_plan=TradePlanConfig(
                safezone_lookback=10,
                safezone_ema_period=22,
                safezone_long_coefficient=2.0,
                safezone_short_coefficient=3.0,
                thermometer_period=22,
                thermometer_target_multiplier=1.0,
            ),
            as_of=pd.Timestamp("2026-04-02 08:00"),
        )

        self.assertIsNotNone(plan)
        self.assertIn("breakout_long", plan.hourly)
        self.assertIn("breakout_short", plan.hourly)
        self.assertEqual(
            [option["code"] for option in plan.hourly["entry_options"]],
            ["EMA_PENETRATION", "PREVIOUS_DAY_BREAK"],
        )
        self.assertTrue(all("exits" in option for option in plan.hourly["entry_options"]))


if __name__ == "__main__":
    unittest.main()
