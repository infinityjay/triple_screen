"""Main backtest runner - orchestrates all 4 phases."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Add src to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@dataclass
class BacktestConfig:
    """Backtest configuration."""

    starting_equity: float = 100000.0
    risk_per_trade_pct: float = 0.02
    max_open_risk_pct: float = 0.06
    leverage_ratio: float = 1.5
    start_date: str = "2023-01-01"
    end_date: str = "2026-05-01"
    model_name: str = "elder_force"
    universe: str = "config/universe_us_top300.yaml"
    symbols: list[str] | None = None
    sqlite_only: bool = True
    max_symbols: int = 0
    include_trade_details: bool = True
    verbose: bool = False
    run_id: str | None = None

    def __post_init__(self) -> None:
        if self.run_id is None:
            self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")


class BacktestRunner:
    """Orchestrates multi-phase backtest execution."""

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.project_root = PROJECT_ROOT
        # Import phases here to avoid circular imports
        from tests.backtest.phases.phase_1_data_acquisition import DataAcquisition
        from tests.backtest.phases.phase_2_engine_setup import EngineSetup
        from tests.backtest.phases.phase_3_execution import BacktestExecution
        from tests.backtest.phases.phase_4_analysis import AnalysisReporting
        
        self.phase1 = DataAcquisition(config)
        self.phase2 = EngineSetup(config)
        self.phase3 = BacktestExecution(config)
        self.phase4 = AnalysisReporting(config)
        self.results: dict[str, Any] = {}

    def run_phase_1(self) -> bool:
        """Phase 1: Data Acquisition."""
        print("\n" + "=" * 60)
        print("PHASE 1: DATA ACQUISITION & PREPARATION")
        print("=" * 60)
        try:
            self.results["phase_1"] = self.phase1.execute()
            print("✓ Phase 1 completed successfully")
            return True
        except Exception as e:
            print(f"✗ Phase 1 failed: {e}")
            if self.config.verbose:
                import traceback
                traceback.print_exc()
            return False

    def run_phase_2(self) -> bool:
        """Phase 2: Engine Setup."""
        print("\n" + "=" * 60)
        print("PHASE 2: BACKTEST ENGINE SETUP")
        print("=" * 60)
        try:
            result = self.phase2.execute()
            self.results["phase_2"] = result
            if isinstance(result, dict) and result.get("status") == "FAILED":
                print(f"✗ Phase 2 failed: {result.get('error')}")
                return False
            print("✓ Phase 2 completed successfully")
            return True
        except Exception as e:
            print(f"✗ Phase 2 failed: {e}")
            if self.config.verbose:
                import traceback
                traceback.print_exc()
            return False

    def run_phase_3(self) -> bool:
        """Phase 3: Execution & Validation."""
        print("\n" + "=" * 60)
        print("PHASE 3: BACKTEST EXECUTION & VALIDATION")
        print("=" * 60)
        try:
            result = self.phase3.execute()
            self.results["phase_3"] = result
            if isinstance(result, dict) and result.get("status") == "FAILED":
                print(f"✗ Phase 3 failed: {result.get('error')}")
                return False
            print("✓ Phase 3 completed successfully")
            return True
        except Exception as e:
            print(f"✗ Phase 3 failed: {e}")
            if self.config.verbose:
                import traceback
                traceback.print_exc()
            return False

    def run_phase_4(self) -> bool:
        """Phase 4: Analysis & Reporting."""
        print("\n" + "=" * 60)
        print("PHASE 4: ANALYSIS & REPORTING")
        print("=" * 60)
        try:
            self.results["phase_4"] = self.phase4.execute(self.results)
            print("✓ Phase 4 completed successfully")
            return True
        except Exception as e:
            print(f"✗ Phase 4 failed: {e}")
            if self.config.verbose:
                import traceback
                traceback.print_exc()
            return False

    def run_all(self) -> bool:
        """Run all 4 phases sequentially."""
        phases = [
            (1, self.run_phase_1),
            (2, self.run_phase_2),
            (3, self.run_phase_3),
            (4, self.run_phase_4),
        ]

        for phase_num, phase_func in phases:
            if not phase_func():
                print(f"\nBacktest halted at Phase {phase_num}")
                return False

        self._save_results()
        self._print_summary()
        return True

    def _save_results(self) -> None:
        """Save results to JSON."""
        output_dir = self.project_root / "data"
        output_dir.mkdir(exist_ok=True)

        results_file = output_dir / f"backtest_{self.config.run_id}_results.json"
        with open(results_file, "w") as f:
            json.dump(self.results, f, indent=2, default=str)
        print(f"\nResults saved to: {results_file}")

    def _print_summary(self) -> None:
        """Print backtest summary."""
        print("\n" + "=" * 60)
        print("BACKTEST SUMMARY")
        print("=" * 60)
        print(f"Run ID: {self.config.run_id}")
        print(f"Model: {self.config.model_name}")
        print(f"Universe: {self.config.universe}")
        print(f"Period: {self.config.start_date} to {self.config.end_date}")
        print(f"Starting Equity: ${self.config.starting_equity:,.0f}")
        print(f"Risk per Trade: {self.config.risk_per_trade_pct*100:.1f}%")
        print(f"Max Open Risk: {self.config.max_open_risk_pct*100:.1f}%")

        # Print key metrics if available
        if phase3_results := self.results.get("phase_3"):
            if metrics := phase3_results.get("summary_metrics"):
                print(f"\nFinal Equity: ${metrics.get('final_equity', 0):,.0f}")
                print(f"Total Trades: {metrics.get('total_trades', 0)}")
                print(f"Win Rate: {metrics.get('win_rate', 0)*100:.1f}%")
                print(f"Max Drawdown: {metrics.get('max_drawdown', 0)*100:.1f}%")
                print(f"Sharpe Ratio: {metrics.get('sharpe_ratio', 0):.2f}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Elder Triple Screen Model Backtest")
    parser.add_argument(
        "--all", action="store_true", help="Run all 4 phases"
    )
    parser.add_argument(
        "--phase", type=int, choices=[1, 2, 3, 4], help="Run specific phase"
    )
    parser.add_argument(
        "--config", type=str, help="Path to custom backtest config YAML"
    )
    parser.add_argument(
        "--symbols", nargs="+", help="Override universe with specific symbols"
    )
    parser.add_argument(
        "--start-date", type=str, help="Override start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date", type=str, help="Override end date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Verbose output"
    )
    parser.add_argument(
        "--prefetch",
        action="store_true",
        help="Fetch missing historical bars from Alpaca instead of using only cached SQLite data",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=0,
        help="Limit the configured universe to the first N symbols",
    )

    args = parser.parse_args()

    # Load or create config
    if args.config:
        from .utils.backtest_fixtures import load_backtest_config
        config = load_backtest_config(args.config)
    else:
        config = BacktestConfig()

    # Override with CLI args
    if args.symbols:
        config.symbols = args.symbols
    if args.start_date:
        config.start_date = args.start_date
    if args.end_date:
        config.end_date = args.end_date
    if args.verbose:
        config.verbose = True
    if args.prefetch:
        config.sqlite_only = False
    if args.max_symbols:
        config.max_symbols = args.max_symbols

    # Run backtest
    runner = BacktestRunner(config)

    if args.all:
        success = runner.run_all()
    elif args.phase:
        method = getattr(runner, f"run_phase_{args.phase}")
        success = method()
    else:
        parser.print_help()
        return

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
