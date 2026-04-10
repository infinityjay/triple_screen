from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.sqlite import SQLiteStorage


class TradeStorageStopFieldTests(unittest.TestCase):
    def test_manual_current_stop_stays_on_trade_and_suggested_stop_comes_from_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = SQLiteStorage(Path(tmp_dir) / "journal.db")
            storage.init_db()

            created = storage.insert_trade(
                {
                    "stock": "AAPL",
                    "direction": "long",
                    "buy_price": 108.5,
                    "shares": 10,
                    "stop_loss": 100.0,
                    "initial_stop_loss": 100.0,
                    "used_stop": 85.0,
                    "buy_date": "2026-04-09",
                }
            )

            storage.update_trade_protective_stop(
                trade_id=str(created["id"]),
                stop_loss=103.0,
                used_stop=55.0,
                stop_basis="SAFEZONE",
                session_date="2026-04-10",
            )
            storage.insert_trade_stop_updates(
                [
                    {
                        "trade_id": str(created["id"]),
                        "symbol": "AAPL",
                        "direction": "long",
                        "session_date": "2026-04-10",
                        "previous_stop_loss": 100.0,
                        "proposed_stop_loss": 103.0,
                        "applied_stop_loss": 103.0,
                        "stop_basis": "SAFEZONE",
                        "changed": True,
                        "status": "UPDATED",
                        "note": "保护性止损已上移",
                    }
                ]
            )

            refreshed = storage.get_trade(str(created["id"]))

        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed["stop_loss"], 100.0)
        self.assertEqual(refreshed["initial_stop_loss"], 100.0)
        self.assertEqual(refreshed["suggested_stop_loss"], 103.0)
        self.assertEqual(refreshed["suggested_stop_basis"], "SAFEZONE")
        self.assertEqual(refreshed["protective_stop_basis"], "SAFEZONE")


if __name__ == "__main__":
    unittest.main()
