from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from universe_optimizer import _load_symbol_rows_from_yaml, rank_candidates, select_candidates


class UniverseOptimizerTests(unittest.TestCase):
    def test_load_symbol_rows_from_yaml_symbols_key(self) -> None:
        text = """
metadata:
  source: test
symbols:
  - ticker: AAPL
    name: Apple
  - symbol: MSFT
    name: Microsoft
  - NVDA
"""
        rows = _load_symbol_rows_from_yaml(text)

        self.assertEqual([row["ticker"] for row in rows], ["AAPL", "MSFT", "NVDA"])
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[1]["rank"], 2)
        self.assertEqual(rows[2]["rank"], 3)

    def test_select_candidates_defaults_to_best_opportunities_without_side_quota(self) -> None:
        metrics_df = pd.DataFrame(
            [
                {
                    "ticker": "LONG1",
                    "name": "Long One",
                    "rank": 1,
                    "close": 120.0,
                    "avg_dollar_volume_20d": 200_000_000.0,
                    "avg_dollar_volume_60d": 180_000_000.0,
                    "volume_expansion_20d_vs_60d": 1.11,
                    "atr_pct_14d": 0.045,
                    "risk_adjusted_momentum_6m": 2.0,
                    "risk_adjusted_momentum_12m": 1.8,
                    "rs_spy_6m_ex_1m": 0.22,
                    "rs_qqq_6m_ex_1m": 0.20,
                    "rs_spy_12m_ex_1m": 0.24,
                    "rs_qqq_12m_ex_1m": 0.23,
                    "ema_fast_gap": 0.07,
                    "ema_slow_gap": 0.15,
                    "ema_stack_gap": 0.08,
                },
                {
                    "ticker": "LONG2",
                    "name": "Long Two",
                    "rank": 2,
                    "close": 95.0,
                    "avg_dollar_volume_20d": 150_000_000.0,
                    "avg_dollar_volume_60d": 145_000_000.0,
                    "volume_expansion_20d_vs_60d": 1.03,
                    "atr_pct_14d": 0.050,
                    "risk_adjusted_momentum_6m": 1.4,
                    "risk_adjusted_momentum_12m": 1.2,
                    "rs_spy_6m_ex_1m": 0.14,
                    "rs_qqq_6m_ex_1m": 0.12,
                    "rs_spy_12m_ex_1m": 0.10,
                    "rs_qqq_12m_ex_1m": 0.09,
                    "ema_fast_gap": 0.04,
                    "ema_slow_gap": 0.08,
                    "ema_stack_gap": 0.04,
                },
                {
                    "ticker": "SHORT1",
                    "name": "Short One",
                    "rank": 3,
                    "close": 80.0,
                    "avg_dollar_volume_20d": 220_000_000.0,
                    "avg_dollar_volume_60d": 210_000_000.0,
                    "volume_expansion_20d_vs_60d": 1.05,
                    "atr_pct_14d": 0.047,
                    "risk_adjusted_momentum_6m": -2.1,
                    "risk_adjusted_momentum_12m": -1.9,
                    "rs_spy_6m_ex_1m": -0.25,
                    "rs_qqq_6m_ex_1m": -0.24,
                    "rs_spy_12m_ex_1m": -0.22,
                    "rs_qqq_12m_ex_1m": -0.21,
                    "ema_fast_gap": -0.09,
                    "ema_slow_gap": -0.18,
                    "ema_stack_gap": -0.10,
                },
                {
                    "ticker": "SHORT2",
                    "name": "Short Two",
                    "rank": 4,
                    "close": 70.0,
                    "avg_dollar_volume_20d": 140_000_000.0,
                    "avg_dollar_volume_60d": 135_000_000.0,
                    "volume_expansion_20d_vs_60d": 1.04,
                    "atr_pct_14d": 0.052,
                    "risk_adjusted_momentum_6m": -1.5,
                    "risk_adjusted_momentum_12m": -1.2,
                    "rs_spy_6m_ex_1m": -0.15,
                    "rs_qqq_6m_ex_1m": -0.16,
                    "rs_spy_12m_ex_1m": -0.12,
                    "rs_qqq_12m_ex_1m": -0.11,
                    "ema_fast_gap": -0.05,
                    "ema_slow_gap": -0.10,
                    "ema_stack_gap": -0.05,
                },
            ]
        )

        ranked = rank_candidates(metrics_df)
        selected = select_candidates(ranked, top_k=2, long_count=0, short_count=0)

        self.assertEqual(len(selected), 2)
        self.assertEqual(set(selected["ticker"]), {"LONG1", "SHORT1"})
        self.assertEqual(int((selected["selection_side"] == "LONG").sum()), 1)
        self.assertEqual(int((selected["selection_side"] == "SHORT").sum()), 1)
        self.assertGreater(
            float(selected.loc[selected["ticker"] == "LONG1", "long_score"].iloc[0]),
            float(selected.loc[selected["ticker"] == "LONG1", "short_score"].iloc[0]),
        )
        self.assertGreater(
            float(selected.loc[selected["ticker"] == "SHORT1", "short_score"].iloc[0]),
            float(selected.loc[selected["ticker"] == "SHORT1", "long_score"].iloc[0]),
        )

    def test_select_candidates_can_still_apply_optional_side_quota(self) -> None:
        metrics_df = pd.DataFrame(
            [
                {
                    "ticker": "LONG1",
                    "name": "Long One",
                    "rank": 1,
                    "close": 120.0,
                    "avg_dollar_volume_20d": 200_000_000.0,
                    "avg_dollar_volume_60d": 180_000_000.0,
                    "volume_expansion_20d_vs_60d": 1.11,
                    "atr_pct_14d": 0.045,
                    "risk_adjusted_momentum_6m": 2.0,
                    "risk_adjusted_momentum_12m": 1.8,
                    "rs_spy_6m_ex_1m": 0.22,
                    "rs_qqq_6m_ex_1m": 0.20,
                    "rs_spy_12m_ex_1m": 0.24,
                    "rs_qqq_12m_ex_1m": 0.23,
                    "ema_fast_gap": 0.07,
                    "ema_slow_gap": 0.15,
                    "ema_stack_gap": 0.08,
                },
                {
                    "ticker": "LONG2",
                    "name": "Long Two",
                    "rank": 2,
                    "close": 95.0,
                    "avg_dollar_volume_20d": 150_000_000.0,
                    "avg_dollar_volume_60d": 145_000_000.0,
                    "volume_expansion_20d_vs_60d": 1.03,
                    "atr_pct_14d": 0.050,
                    "risk_adjusted_momentum_6m": 1.4,
                    "risk_adjusted_momentum_12m": 1.2,
                    "rs_spy_6m_ex_1m": 0.14,
                    "rs_qqq_6m_ex_1m": 0.12,
                    "rs_spy_12m_ex_1m": 0.10,
                    "rs_qqq_12m_ex_1m": 0.09,
                    "ema_fast_gap": 0.04,
                    "ema_slow_gap": 0.08,
                    "ema_stack_gap": 0.04,
                },
                {
                    "ticker": "SHORT1",
                    "name": "Short One",
                    "rank": 3,
                    "close": 80.0,
                    "avg_dollar_volume_20d": 220_000_000.0,
                    "avg_dollar_volume_60d": 210_000_000.0,
                    "volume_expansion_20d_vs_60d": 1.05,
                    "atr_pct_14d": 0.047,
                    "risk_adjusted_momentum_6m": -2.1,
                    "risk_adjusted_momentum_12m": -1.9,
                    "rs_spy_6m_ex_1m": -0.25,
                    "rs_qqq_6m_ex_1m": -0.24,
                    "rs_spy_12m_ex_1m": -0.22,
                    "rs_qqq_12m_ex_1m": -0.21,
                    "ema_fast_gap": -0.09,
                    "ema_slow_gap": -0.18,
                    "ema_stack_gap": -0.10,
                },
                {
                    "ticker": "SHORT2",
                    "name": "Short Two",
                    "rank": 4,
                    "close": 70.0,
                    "avg_dollar_volume_20d": 140_000_000.0,
                    "avg_dollar_volume_60d": 135_000_000.0,
                    "volume_expansion_20d_vs_60d": 1.04,
                    "atr_pct_14d": 0.052,
                    "risk_adjusted_momentum_6m": -1.5,
                    "risk_adjusted_momentum_12m": -1.2,
                    "rs_spy_6m_ex_1m": -0.15,
                    "rs_qqq_6m_ex_1m": -0.16,
                    "rs_spy_12m_ex_1m": -0.12,
                    "rs_qqq_12m_ex_1m": -0.11,
                    "ema_fast_gap": -0.05,
                    "ema_slow_gap": -0.10,
                    "ema_stack_gap": -0.05,
                },
            ]
        )

        ranked = rank_candidates(metrics_df)
        selected = select_candidates(ranked, top_k=4, long_count=2, short_count=2)

        self.assertEqual(len(selected), 4)
        self.assertEqual(set(selected["ticker"]), {"LONG1", "LONG2", "SHORT1", "SHORT2"})
        self.assertEqual(int((selected["selection_side"] == "LONG").sum()), 2)
        self.assertEqual(int((selected["selection_side"] == "SHORT").sum()), 2)


if __name__ == "__main__":
    unittest.main()
