"""Data validation utilities."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class DataValidator:
    """Validates backtest data."""

    @staticmethod
    def validate_bars_count(symbol: str, daily_count: int, weekly_count: int) -> dict[str, Any]:
        """Check if bar counts are reasonable."""
        expected_daily = 756  # ~3 years of trading days
        expected_weekly = 208
        
        daily_valid = abs(daily_count - expected_daily) < 20  # Allow some variance for holidays
        weekly_valid = abs(weekly_count - expected_weekly) < 5
        
        return {
            "symbol": symbol,
            "daily_bars": {
                "expected": expected_daily,
                "actual": daily_count,
                "variance": daily_count - expected_daily,
                "valid": daily_valid,
            },
            "weekly_bars": {
                "expected": expected_weekly,
                "actual": weekly_count,
                "variance": weekly_count - expected_weekly,
                "valid": weekly_valid,
            },
            "overall_valid": daily_valid and weekly_valid,
        }
    
    @staticmethod
    def validate_ohlcv_data(bars: list[dict[str, Any]]) -> dict[str, Any]:
        """Check for NaNs, gaps, and data quality."""
        import pandas as pd
        import numpy as np
        
        if not bars:
            return {"status": "INVALID", "issues": ["No bars provided"]}
        
        issues = []
        
        # Check for required fields
        required_fields = ["open", "high", "low", "close", "volume"]
        for field in required_fields:
            if not all(field in bar for bar in bars):
                issues.append(f"Missing field: {field}")
        
        # Check for NaNs
        for i, bar in enumerate(bars):
            for field in required_fields:
                value = bar.get(field)
                if value is None or (isinstance(value, float) and np.isnan(value)):
                    issues.append(f"NaN at bar {i}, field {field}")
        
        # Check OHLC relationship (high >= low, high >= open/close, low <= open/close)
        for i, bar in enumerate(bars):
            high = float(bar.get("high", 0))
            low = float(bar.get("low", 0))
            open_price = float(bar.get("open", 0))
            close = float(bar.get("close", 0))
            
            if high < low:
                issues.append(f"Invalid bar {i}: high < low ({high} < {low})")
            if high < max(open_price, close):
                issues.append(f"Invalid bar {i}: high < open/close")
            if low > min(open_price, close):
                issues.append(f"Invalid bar {i}: low > open/close")
        
        status = "VALID" if not issues else "INVALID"
        
        return {
            "status": status,
            "total_bars": len(bars),
            "issues": issues,
            "issue_count": len(issues),
        }
    
    @staticmethod
    def validate_date_range(bars: list[dict[str, Any]], expected_start: str, expected_end: str) -> dict[str, Any]:
        """Validate date range coverage."""
        from datetime import datetime
        
        if not bars:
            return {"status": "INVALID", "issues": ["No bars provided"]}
        
        issues = []
        
        # Extract dates (assuming 'timestamp' or 'date' field)
        dates = []
        for bar in bars:
            timestamp = bar.get("timestamp") or bar.get("date")
            if timestamp:
                try:
                    if isinstance(timestamp, str):
                        date = datetime.fromisoformat(timestamp.split("T")[0])
                    else:
                        date = timestamp
                    dates.append(date)
                except Exception:
                    issues.append(f"Could not parse date: {timestamp}")
        
        if not dates:
            return {"status": "INVALID", "issues": ["No valid dates found in bars"]}
        
        dates_sorted = sorted(dates)
        
        try:
            expected_start_dt = datetime.fromisoformat(expected_start)
            expected_end_dt = datetime.fromisoformat(expected_end)
        except Exception:
            issues.append(f"Invalid expected date range format")
            return {"status": "INVALID", "issues": issues}
        
        # Check coverage
        actual_start = dates_sorted[0]
        actual_end = dates_sorted[-1]
        
        if actual_start > expected_start_dt:
            issues.append(f"Start date gap: expected {expected_start}, got {actual_start.date()}")
        if actual_end < expected_end_dt:
            issues.append(f"End date gap: expected {expected_end}, got {actual_end.date()}")
        
        status = "VALID" if not issues else "PARTIAL"
        
        return {
            "status": status,
            "expected_start": expected_start,
            "expected_end": expected_end,
            "actual_start": actual_start.date().isoformat(),
            "actual_end": actual_end.date().isoformat(),
            "issues": issues,
        }
