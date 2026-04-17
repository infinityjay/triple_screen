from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backtest_triple_screen import (
    Position,
    compute_position_open_risk,
    compute_position_size,
    compute_remaining_stop_budget,
)
from storage.sqlite import SQLiteStorage


class BacktestRiskBudgetTests(unittest.TestCase):
    def test_position_size_respects_per_trade_risk_before_cash_cap(self) -> None:
        sizing = compute_position_size(
            account_equity=10_000.0,
            cash_available=10_000.0,
            risk_pct=2.0,
            remaining_stop_budget=600.0,
            risk_per_share=10.0,
            entry_price=300.0,
        )

        self.assertEqual(sizing.shares, 20)
        self.assertEqual(sizing.used_risk, 200.0)
        self.assertEqual(sizing.position_cost, 6000.0)
        self.assertEqual(sizing.allowed_risk, 200.0)

    def test_position_size_respects_cash_cap(self) -> None:
        sizing = compute_position_size(
            account_equity=10_000.0,
            cash_available=10_000.0,
            risk_pct=2.0,
            remaining_stop_budget=600.0,
            risk_per_share=1.0,
            entry_price=400.0,
        )

        self.assertEqual(sizing.shares, 25)
        self.assertEqual(sizing.used_risk, 25.0)
        self.assertEqual(sizing.position_cost, 10_000.0)

    def test_remaining_stop_budget_uses_current_open_risk(self) -> None:
        open_positions = {
            "AAPL": Position(
                symbol="AAPL",
                direction="LONG",
                entry_session_date="2026-04-10",
                entry_timestamp="2026-04-10T14:30:00+00:00",
                entry_price=100.0,
                initial_stop=95.0,
                active_stop=96.0,
                risk_per_share=5.0,
                shares=20,
                take_profit=115.0,
                source_session_date="2026-04-09",
                bars_held=1,
                position_cost=2000.0,
                last_price=101.0,
                entry_cash_before=10_000.0,
                entry_equity_before=10_000.0,
                entry_open_risk_before=0.0,
                entry_remaining_stop_budget=600.0,
                entry_allowed_risk=200.0,
            ),
            "MSFT": Position(
                symbol="MSFT",
                direction="LONG",
                entry_session_date="2026-04-10",
                entry_timestamp="2026-04-10T15:30:00+00:00",
                entry_price=50.0,
                initial_stop=47.0,
                active_stop=48.0,
                risk_per_share=3.0,
                shares=30,
                take_profit=59.0,
                source_session_date="2026-04-09",
                bars_held=1,
                position_cost=1500.0,
                last_price=52.0,
                entry_cash_before=8000.0,
                entry_equity_before=10_000.0,
                entry_open_risk_before=80.0,
                entry_remaining_stop_budget=520.0,
                entry_allowed_risk=200.0,
            ),
        }

        self.assertEqual(compute_position_open_risk(open_positions["AAPL"]), 80.0)
        self.assertEqual(compute_position_open_risk(open_positions["MSFT"]), 60.0)
        self.assertEqual(compute_remaining_stop_budget(10_000.0, 6.0, open_positions), 460.0)

    def test_backtest_results_can_be_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "journal.db")
            storage.init_db()
            run_id = storage.insert_backtest_run(
                {
                    "start_date": "2026-01-01",
                    "end_date": "2026-04-01",
                    "initial_capital": 10_000.0,
                    "risk_pct": 2.0,
                    "max_total_open_risk_pct": 6.0,
                    "max_open_positions": 2,
                    "assumptions": {"position_sizing": "test"},
                    "summary": {"total_return_pct": 12.3},
                },
                [
                    {
                        "symbol": "AAPL",
                        "direction": "LONG",
                        "entry_timestamp": "2026-02-01T15:30:00+00:00",
                        "exit_timestamp": "2026-02-05T20:00:00+00:00",
                        "entry_price": 100.0,
                        "exit_price": 110.0,
                        "initial_stop": 95.0,
                        "final_stop": 102.0,
                        "shares": 20,
                        "pnl": 200.0,
                        "pnl_pct": 10.0,
                        "r_multiple": 2.0,
                        "exit_reason": "STOP",
                        "position_cost": 2000.0,
                        "entry_cash_before": 10_000.0,
                        "entry_equity_before": 10_000.0,
                        "entry_open_risk_before": 0.0,
                        "entry_remaining_stop_budget": 600.0,
                        "entry_allowed_risk": 200.0,
                    }
                ],
            )

            with storage._connect() as connection:
                run_row = connection.execute("SELECT id FROM backtest_runs WHERE id = ?", (run_id,)).fetchone()
                trade_row = connection.execute(
                    "SELECT symbol, entry_remaining_stop_budget FROM backtest_trades WHERE run_id = ?",
                    (run_id,),
                ).fetchone()

        self.assertIsNotNone(run_row)
        self.assertEqual(trade_row["symbol"], "AAPL")
        self.assertEqual(trade_row["entry_remaining_stop_budget"], 600.0)


if __name__ == "__main__":
    unittest.main()
