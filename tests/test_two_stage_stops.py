from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

import indicators
from journal.service import JournalManager
from schema import TradePlanConfig


def _daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [101.0, 104.0, 106.0],
            "high": [106.0, 107.0, 108.0],
            "low": [100.0, 102.0, 103.0],
            "close": [105.0, 106.0, 107.0],
        },
        index=pd.to_datetime(["2026-04-07", "2026-04-08", "2026-04-09"]),
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


class _FakeMarketData:
    def get_daily_bars(self, symbol: str):
        return _daily_frame()


class TwoStageStopTests(unittest.TestCase):
    def test_calc_exits_separates_initial_and_protective_stop(self) -> None:
        exits = indicators.calc_exits(
            direction="LONG",
            entry=108.5,
            daily_frame=_daily_frame(),
            atr=1.0,
            trade_plan=TradePlanConfig(
                safezone_lookback=10,
                safezone_coefficient=2.0,
                thermometer_period=22,
                thermometer_target_multiplier=1.0,
            ),
            signal_bar_high=108.0,
            signal_bar_low=104.0,
        )

        self.assertEqual(exits["initial_stop_signal_bar"], 104.0)
        self.assertEqual(exits["initial_stop_pullback_pivot"], 100.0)
        self.assertEqual(exits["initial_stop_loss"], 100.0)
        self.assertEqual(exits["initial_stop_basis"], "PULLBACK_PIVOT")
        self.assertEqual(exits["protective_stop_loss"], 103.0)
        self.assertEqual(exits["protective_stop_basis"], "SAFEZONE")
        self.assertEqual(exits["stop_loss"], 100.0)
        self.assertEqual(exits["risk_per_share"], 8.5)

    def test_open_position_stop_updates_use_safezone_only(self) -> None:
        manager = JournalManager(
            storage=_FakeStorage(),
            market_data=_FakeMarketData(),
            trade_plan=TradePlanConfig(
                safezone_lookback=10,
                safezone_coefficient=2.0,
                thermometer_period=22,
                thermometer_target_multiplier=1.0,
            ),
        )

        summary = manager.preview_open_position_stops(session_date=date(2026, 4, 9))

        self.assertEqual(summary.updated_count, 1)
        self.assertEqual(summary.updates[0]["previous_stop_loss"], 101.0)
        self.assertEqual(summary.updates[0]["proposed_stop_loss"], 103.0)
        self.assertEqual(summary.updates[0]["applied_stop_loss"], 103.0)
        self.assertEqual(summary.updates[0]["stop_basis"], "SAFEZONE")


if __name__ == "__main__":
    unittest.main()
