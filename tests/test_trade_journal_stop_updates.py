from __future__ import annotations

import unittest

from journal import apply_monotonic_stop, compute_used_stop


class TradeJournalStopUpdateTests(unittest.TestCase):
    def test_long_stop_only_moves_higher(self) -> None:
        self.assertEqual(apply_monotonic_stop(95.0, 101.5, "long"), 101.5)
        self.assertEqual(apply_monotonic_stop(95.0, 90.0, "long"), 95.0)

    def test_short_stop_only_moves_lower(self) -> None:
        self.assertEqual(apply_monotonic_stop(105.0, 101.5, "short"), 101.5)
        self.assertEqual(apply_monotonic_stop(105.0, 110.0, "short"), 105.0)

    def test_used_stop_respects_direction(self) -> None:
        self.assertEqual(compute_used_stop(100.0, 95.0, 20, "long"), 100.0)
        self.assertEqual(compute_used_stop(100.0, 104.0, 20, "short"), 80.0)
        self.assertEqual(compute_used_stop(100.0, 102.0, 20, "long"), 0.0)
        self.assertEqual(compute_used_stop(100.0, 98.0, 20, "short"), 0.0)
        # Test when stop equals entry
        self.assertEqual(compute_used_stop(100.0, 100.0, 20, "long"), 0.0)
        self.assertEqual(compute_used_stop(100.0, 100.0, 20, "short"), 0.0)
        # Test when stop is above entry for long
        self.assertEqual(compute_used_stop(100.0, 105.0, 20, "long"), 0.0)
        # Test when stop is below entry for short
        self.assertEqual(compute_used_stop(100.0, 95.0, 20, "short"), 0.0)


if __name__ == "__main__":
    unittest.main()
