"""Phase 1: Data Acquisition & Preparation."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class DataAcquisition:
    """Downloads and validates historical data."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.project_root = PROJECT_ROOT
        
    def execute(self) -> dict[str, Any]:
        """Execute Phase 1: Data readiness validation."""
        print("\n1. Loading configuration...")
        try:
            from config.loader import load_settings
            settings = load_settings()
            print("   ✓ Settings loaded")
        except Exception as e:
            print(f"   ⚠ Could not load config: {e}")
            settings = None
        
        print("2. Checking database...")
        database_path = None
        storage = None
        if settings is not None:
            from storage.sqlite import SQLiteStorage

            database_path = settings.storage.database_path
            storage = SQLiteStorage(database_path)
            storage.init_db()
            print(f"   ✓ Database ready: {database_path}")
        else:
            print("   ⚠ Settings unavailable; database completeness cannot be checked")
        
        print("3. Loading universe symbols...")
        symbols = self._load_symbols()
        if self.config.max_symbols > 0:
            symbols = symbols[: self.config.max_symbols]
        print(f"   → Loaded {len(symbols)} symbols from {self.config.universe}")
        
        if settings is not None and not self.config.sqlite_only:
            print("4. Prefetching historical data from Alpaca...")
            try:
                from backtest_triple_screen import prefetch_history
                from config.schema import UniverseConfig

                fetch_settings = self._settings_with_symbol_override(settings, UniverseConfig)
                prefetch_result = prefetch_history(
                    settings=fetch_settings,
                    start_date=date.fromisoformat(self.config.start_date),
                    end_date=date.fromisoformat(self.config.end_date),
                    max_symbols=self.config.max_symbols,
                )
                print(f"   ✓ Prefetch complete for {prefetch_result['symbol_count']} symbols")
            except Exception as e:
                print(f"   ✗ Prefetch failed: {e}")
                raise
        else:
            print("4. Validating cached historical data...")
        print(f"   → Date range: {self.config.start_date} to {self.config.end_date}")
        print("   → Timeframes: day, week, hour")

        completeness = self._check_completeness(storage, symbols)
        if completeness["checked"]:
            print(
                "   → Cached coverage: "
                f"{completeness['complete_symbols']}/{completeness['symbols_checked']} symbols complete"
            )
            if completeness["missing_or_partial_symbols"]:
                preview = ", ".join(completeness["missing_or_partial_symbols"][:10])
                print(f"   ⚠ Missing/partial symbols: {preview}")
        else:
            print("   ⚠ Skipped cache coverage check")
        
        download_stats = {
            "symbols_count": len(symbols),
            "start_date": self.config.start_date,
            "end_date": self.config.end_date,
            "database_path": str(database_path) if database_path else None,
            "complete_symbols": completeness["complete_symbols"],
            "missing_or_partial_symbols": completeness["missing_or_partial_symbols"],
            "symbols": symbols if self.config.verbose else f"{len(symbols)} symbols",
        }
        
        print("5. Validation checklist:")
        print("   ✓ Symbol list loaded")
        print("   ✓ Date range configured")
        print("   ✓ Database path available" if database_path else "   ⚠ Database path unavailable")
        if completeness["missing_or_partial_symbols"]:
            print("   ⚠ Historical bars need prefetch before Phase 3 can run this universe")
        else:
            print("   ✓ Cached historical bars available for requested universe")
        
        validation_report = {
            "total_symbols": len(symbols),
            "start_date": self.config.start_date,
            "end_date": self.config.end_date,
            "status": "DATA_READY" if completeness["checked"] and not completeness["missing_or_partial_symbols"] else "READY_FOR_PREFETCH",
        }
        
        print("\n✓ Phase 1 Summary:")
        print(f"  → Universe: {self.config.universe}")
        print(f"  → Symbols: {len(symbols)}")
        print(f"  → Date range: {self.config.start_date} to {self.config.end_date}")
        print(f"  → Status: {validation_report['status']}")
        if validation_report["status"] == "READY_FOR_PREFETCH":
            print("  → Next: run with --prefetch or use src/backtest_triple_screen.py --prefetch-only")
        
        return {
            "download_stats": download_stats,
            "validation_report": validation_report,
            "timestamp": datetime.now().isoformat(),
        }

    def _check_completeness(self, storage: Any, symbols: list[str]) -> dict[str, Any]:
        """Check whether SQLite has bars for each requested symbol/timeframe."""
        if storage is None:
            return {
                "checked": False,
                "symbols_checked": len(symbols),
                "complete_symbols": 0,
                "missing_or_partial_symbols": symbols,
                "details": {},
            }

        required_timeframes = ["day", "week", "hour"]
        expected_start = datetime.fromisoformat(self.config.start_date).date()
        expected_end = datetime.fromisoformat(self.config.end_date).date()
        details: dict[str, dict[str, dict[str, Any]]] = {}
        missing_or_partial: list[str] = []
        for symbol in symbols:
            counts: dict[str, dict[str, Any]] = {}
            complete = True
            for timeframe in required_timeframes:
                frame = storage.get_price_bars(symbol, timeframe)
                count = 0 if frame is None else len(frame)
                first_date = None
                last_date = None
                covers_range = False
                if frame is not None and not frame.empty:
                    first_date = frame.index.min().date().isoformat()
                    last_date = frame.index.max().date().isoformat()
                    covers_range = frame.index.min().date() <= expected_start and frame.index.max().date() >= expected_end
                counts[timeframe] = {
                    "bars": count,
                    "first_date": first_date,
                    "last_date": last_date,
                    "covers_requested_range": covers_range,
                }
                if count == 0 or not covers_range:
                    complete = False
            details[symbol] = counts
            if not complete:
                missing_or_partial.append(symbol)

        return {
            "checked": True,
            "symbols_checked": len(symbols),
            "complete_symbols": len(symbols) - len(missing_or_partial),
            "missing_or_partial_symbols": missing_or_partial,
            "details": details if self.config.verbose else {},
        }
    
    def _load_symbols(self) -> list[str]:
        """Load symbols from config or universe file."""
        if self.config.symbols:
            return self.config.symbols
        
        # Try to load from universe file
        try:
            import yaml
            universe_path = Path(self.config.universe)
            if not universe_path.is_absolute():
                universe_path = self.project_root / universe_path
            if universe_path.exists():
                with open(universe_path) as f:
                    config_dict = yaml.safe_load(f)
                    rows = config_dict.get("symbols", [])
                    symbols = [
                        str(item.get("ticker") or item.get("symbol")).strip().upper()
                        for item in rows
                        if item.get("ticker") or item.get("symbol")
                    ]
                    return symbols
            print(f"   ⚠ Universe file not found: {universe_path}")
            return ["AAPL", "MSFT", "TSLA", "AMZN", "NVDA"]
        except Exception as e:
            print(f"   ⚠ Could not load universe file: {e}")
            return ["AAPL", "MSFT", "TSLA", "AMZN", "NVDA"]

    def _settings_with_symbol_override(self, settings: Any, universe_cls: Any) -> Any:
        """Return settings with CLI symbols applied as a custom universe."""
        if not self.config.symbols:
            return settings
        universe = universe_cls(
            mode="custom",
            top_n=len(self.config.symbols),
            static_file=None,
            symbols=[],
            custom_symbols=[symbol.upper() for symbol in self.config.symbols],
            allowed_ticker_types=settings.universe.allowed_ticker_types,
            exclude_symbols_containing=settings.universe.exclude_symbols_containing,
        )
        return replace(settings, universe=universe)
