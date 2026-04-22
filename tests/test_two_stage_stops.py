from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

import indicators
from journal.service import JournalManager, compute_open_profit, compute_stop_locked_profit, compute_profit_capture_pct
from config.schema import TradePlanConfig


def _daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [101.0, 101.0, 101.0],
            "low": [100.0, 99.0, 98.0],
            "close": [100.0, 100.0, 100.0],
        },
        index=pd.to_datetime(["2026-04-07", "2026-04-08", "2026-04-09"]),
    )


def _daily_frame_lower_safezone() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [106.0, 106.0, 106.0],
            "low": [104.8, 105.0, 104.9],
            "close": [105.0, 105.0, 105.0],
        },
        index=pd.to_datetime(["2026-04-07", "2026-04-08", "2026-04-09"]),
    )


def _daily_frame_short_safezone() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [101.0, 102.0, 100.0, 103.0],
            "low": [99.0, 98.0, 97.0, 96.0],
            "close": [100.0, 100.0, 100.0, 100.0],
        },
        index=pd.to_datetime(["2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09"]),
    )


def _daily_frame_nick_long() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [109.0, 108.0, 106.0, 102.0, 100.0, 101.0, 99.0, 102.0, 112.0],
            "high": [110.0, 111.0, 112.0, 111.0, 109.0, 108.0, 109.0, 110.0, 113.0],
            "low": [108.0, 107.0, 104.0, 100.0, 98.0, 99.0, 97.0, 100.0, 110.0],
            "close": [109.0, 108.0, 105.0, 101.0, 99.0, 100.0, 98.0, 101.0, 112.0],
        },
        index=pd.to_datetime(
            ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10", "2026-04-13"]
        ),
    )


def _daily_frame_nick_short() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [101.0, 100.0, 99.0, 101.0, 103.0, 102.0, 104.0, 103.0, 97.0],
            "high": [102.0, 104.0, 106.0, 108.0, 110.0, 109.0, 111.0, 108.0, 100.0],
            "low": [100.0, 99.0, 98.0, 99.0, 101.0, 100.0, 102.0, 103.0, 96.0],
            "close": [101.0, 100.0, 99.0, 101.0, 103.0, 102.0, 104.0, 103.0, 97.0],
        },
        index=pd.to_datetime(
            ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10", "2026-04-13"]
        ),
    )


class _FakeStorage:
    def list_open_trades(self) -> list[dict]:
        return [
            {
                "id": "trade-1",
                "stock": "AAPL",
                "direction": "long",
                "buy_price": 108.5,
                "shares": 10,
                "stop_loss": 101.0,
                "initial_stop_loss": 100.0,
                "initial_stop_basis": "PULLBACK_PIVOT",
            }
        ]


class _FakeStorageWithSuggestedStop:
    def list_open_trades(self) -> list[dict]:
        return [
            {
                "id": "trade-2",
                "stock": "MSFT",
                "direction": "long",
                "buy_price": 108.5,
                "shares": 10,
                "stop_loss": 100.0,
                "initial_stop_loss": 100.0,
                "initial_stop_basis": "PULLBACK_PIVOT",
                "suggested_stop_loss": 103.0,
            }
        ]


class _FakeStorageProfitableWithTightPreviousStop:
    def list_open_trades(self) -> list[dict]:
        return [
            {
                "id": "trade-3",
                "stock": "NVDA",
                "direction": "long",
                "buy_price": 100.0,
                "shares": 10,
                "stop_loss": 120.0,
                "initial_stop_loss": 95.0,
                "initial_stop_basis": "SAFEZONE",
                "suggested_stop_loss": 120.0,
            }
        ]


class _FakeMarketData:
    def get_daily_bars(self, symbol: str):
        return _daily_frame()


class _FakeMarketDataLowerSafezone:
    def get_daily_bars(self, symbol: str):
        return _daily_frame_lower_safezone()


class _FakeMarketDataProfitableWarning:
    def get_daily_bars(self, symbol: str):
        return pd.DataFrame(
            {
                "open": [119.5, 111.0],
                "high": [120.0, 130.0],
                "low": [119.0, 110.0],
                "close": [119.5, 130.0],
            },
            index=pd.to_datetime(["2026-04-08", "2026-04-09"]),
        )


class TwoStageStopTests(unittest.TestCase):
    def test_calc_exits_separates_initial_and_protective_stop(self) -> None:
        exits = indicators.calc_exits(
            direction="LONG",
            entry=108.5,
            daily_frame=_daily_frame(),
            atr=1.0,
            trade_plan=TradePlanConfig(
                safezone_lookback=10,
                safezone_ema_period=22,
                safezone_long_coefficient=2.0,
                safezone_short_coefficient=3.0,
                thermometer_period=22,
                thermometer_target_multiplier=1.0,
            ),
            signal_bar_high=108.0,
            signal_bar_low=104.0,
        )

        self.assertIsNone(exits["initial_stop_signal_bar"])
        self.assertEqual(exits["initial_stop_safezone"], 97.0)
        self.assertEqual(exits["initial_stop_nick"], 98.99)
        self.assertIsNone(exits["initial_stop_pullback_pivot"])
        self.assertIsNone(exits["initial_stop_loss"])
        self.assertEqual(exits["initial_stop_basis"], "CHOICE_REQUIRED")
        self.assertEqual(exits["initial_stop_model_loss"], 97.0)
        self.assertEqual(exits["initial_stop_model_basis"], "SAFEZONE")
        self.assertEqual(exits["protective_stop_loss"], 96.7908)
        self.assertEqual(exits["protective_stop_basis"], "ATR_1X")
        self.assertIsNone(exits["stop_loss"])
        self.assertEqual(exits["stop_loss_atr_1x"], 96.7908)
        self.assertEqual(exits["stop_loss_atr_2x"], 95.5816)
        self.assertIsNone(exits["risk_per_share"])
        self.assertEqual(exits["risk_per_share_model"], 11.5)
        self.assertIsNone(exits["reward_risk_ratio"])
        self.assertEqual(exits["reward_risk_ratio_model"], 0.01)

    def test_open_position_stop_updates_use_safezone_only(self) -> None:
        manager = JournalManager(
            storage=_FakeStorage(),
            market_data=_FakeMarketData(),
            trade_plan=TradePlanConfig(
                safezone_lookback=10,
                safezone_ema_period=22,
                safezone_long_coefficient=2.0,
                safezone_short_coefficient=3.0,
                thermometer_period=22,
                thermometer_target_multiplier=1.0,
            ),
        )

        summary = manager.preview_open_position_stops(session_date=date(2026, 4, 9))

        self.assertEqual(summary.updated_count, 1)
        self.assertEqual(summary.unchanged_count, 0)
        self.assertEqual(summary.updates[0]["previous_stop_loss"], 101.0)
        self.assertEqual(summary.updates[0]["proposed_stop_loss"], 96.7908)
        self.assertEqual(summary.updates[0]["proposed_stop_loss_atr_2x"], 95.5816)
        self.assertEqual(summary.updates[0]["applied_stop_loss"], 96.7908)
        self.assertEqual(summary.updates[0]["latest_close"], 100.0)
        self.assertEqual(summary.updates[0]["open_profit"], -85.0)
        self.assertEqual(summary.updates[0]["locked_profit_atr_1x"], -117.0918)
        self.assertEqual(summary.updates[0]["locked_profit_atr_2x"], -129.1837)
        self.assertIsNone(summary.updates[0]["profit_capture_pct_atr_1x"])
        self.assertIsNone(summary.updates[0]["profit_capture_pct_atr_2x"])
        self.assertEqual(summary.updates[0]["stop_basis"], "ATR_1X")

    def test_open_position_stop_updates_keep_previous_suggested_stop_separate_from_manual_stop(self) -> None:
        manager = JournalManager(
            storage=_FakeStorageWithSuggestedStop(),
            market_data=_FakeMarketDataLowerSafezone(),
            trade_plan=TradePlanConfig(
                safezone_lookback=10,
                safezone_ema_period=22,
                safezone_long_coefficient=2.0,
                safezone_short_coefficient=3.0,
                thermometer_period=22,
                thermometer_target_multiplier=1.0,
            ),
        )

        summary = manager.preview_open_position_stops(session_date=date(2026, 4, 9))

        self.assertEqual(summary.updated_count, 1)
        self.assertEqual(summary.unchanged_count, 0)
        self.assertEqual(summary.updates[0]["previous_stop_loss"], 103.0)
        self.assertEqual(summary.updates[0]["proposed_stop_loss"], 103.7204)
        self.assertEqual(summary.updates[0]["proposed_stop_loss_atr_2x"], 102.5408)
        self.assertEqual(summary.updates[0]["applied_stop_loss"], 103.7204)
        self.assertEqual(summary.updates[0]["latest_close"], 105.0)
        self.assertEqual(summary.updates[0]["open_profit"], -35.0)
        self.assertEqual(summary.updates[0]["locked_profit_atr_1x"], -47.7959)
        self.assertEqual(summary.updates[0]["locked_profit_atr_2x"], -59.5918)
        self.assertIsNone(summary.updates[0]["profit_capture_pct_atr_1x"])
        self.assertIsNone(summary.updates[0]["profit_capture_pct_atr_2x"])
        self.assertEqual(summary.updates[0]["stop_basis"], "ATR_1X")

    def test_open_position_stop_updates_warn_and_block_looser_stop_when_capture_below_one_third(self) -> None:
        manager = JournalManager(
            storage=_FakeStorageProfitableWithTightPreviousStop(),
            market_data=_FakeMarketDataProfitableWarning(),
            trade_plan=TradePlanConfig(
                safezone_lookback=10,
                safezone_ema_period=22,
                safezone_long_coefficient=2.0,
                safezone_short_coefficient=3.0,
                thermometer_period=22,
                thermometer_target_multiplier=1.0,
            ),
        )

        summary = manager.preview_open_position_stops(session_date=date(2026, 4, 9))

        self.assertEqual(summary.updated_count, 0)
        self.assertEqual(summary.unchanged_count, 1)
        self.assertEqual(summary.updates[0]["previous_stop_loss"], 120.0)
        self.assertLess(summary.updates[0]["proposed_stop_loss"], 120.0)
        self.assertEqual(summary.updates[0]["applied_stop_loss"], 120.0)
        self.assertEqual(summary.updates[0]["open_profit"], 300.0)
        self.assertLess(summary.updates[0]["profit_capture_pct_atr_1x"], 33.33)
        self.assertTrue(summary.updates[0]["warning_triggered"])
        self.assertEqual(summary.updates[0]["status"], "WARNING")
        self.assertIn("超过 2/3 浮盈", summary.updates[0]["note"])

    def test_calc_safezone_stop_uses_wider_short_coefficient(self) -> None:
        stop, noise = indicators.calc_safezone_stop(
            _daily_frame_short_safezone(),
            "SHORT",
            TradePlanConfig(
                safezone_lookback=10,
                safezone_ema_period=22,
                safezone_long_coefficient=2.0,
                safezone_short_coefficient=3.0,
                thermometer_period=22,
                thermometer_target_multiplier=1.0,
            ),
        )

        self.assertEqual(noise, 2.0)
        self.assertEqual(stop, 106.0)

    def test_calc_nick_stop_uses_second_low_for_bottom_structure(self) -> None:
        stop = indicators.calc_nick_stop(_daily_frame_nick_long(), "LONG")
        self.assertEqual(stop, 97.99)

    def test_calc_nick_stop_uses_second_low_from_recent_daily_window(self) -> None:
        older_index = pd.date_range("2026-02-02", periods=25, freq="B")
        older = pd.DataFrame(
            {
                "open": [200.0] * len(older_index),
                "high": [205.0] * len(older_index),
                "low": [1.0] + [2.0] + [190.0] * (len(older_index) - 2),
                "close": [204.0] * len(older_index),
            },
            index=older_index,
        )
        recent = _daily_frame_nick_long()

        stop = indicators.calc_nick_stop(pd.concat([older, recent]), "LONG")

        self.assertEqual(stop, 97.99)

    def test_calc_nick_stop_uses_second_high_for_top_structure(self) -> None:
        stop = indicators.calc_nick_stop(_daily_frame_nick_short(), "SHORT")
        self.assertEqual(stop, 110.01)

    def test_profit_capture_metrics_report_percent_of_open_profit(self) -> None:
        open_profit = compute_open_profit(100.0, 110.0, 10, "long")
        locked_profit = compute_stop_locked_profit(100.0, 107.0, 10, "long")
        capture_pct = compute_profit_capture_pct(open_profit, locked_profit)

        self.assertEqual(open_profit, 100.0)
        self.assertEqual(locked_profit, 70.0)
        self.assertEqual(capture_pct, 70.0)


if __name__ == "__main__":
    unittest.main()
