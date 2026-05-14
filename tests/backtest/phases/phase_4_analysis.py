"""Phase 4: Analysis & Reporting."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class AnalysisReporting:
    """Generates analysis reports and visualizations."""

    def __init__(self, config: Any) -> None:
        self.config = config
        
    def execute(self, results: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute Phase 4: Analysis & reporting."""
        print("\n1. Loading backtest results...")
        results = results or {}
        phase3 = results.get("phase_3", {})
        trades = phase3.get("trades", [])
        if isinstance(trades, str):
            trades = []
        if phase3.get("status") == "FAILED":
            return {"status": "FAILED", "error": "Phase 3 did not complete", "timestamp": datetime.now().isoformat()}
        print("   ✓ Results loaded")
        
        print("2. Computing equity curve...")
        equity_curve = self._build_equity_curve(trades)
        drawdown = self._calculate_drawdown(equity_curve)
        print(f"   → Equity progression points: {len(equity_curve)}")
        print(f"   → Max drawdown: {drawdown.get('max_drawdown_pct', 0.0)*100:.1f}%")
        print("   ✓ Equity curve ready")
        
        print("3. Generating P&L breakdown...")
        monthly_pnl = self._pnl_by_period(trades, period="month")
        yearly_pnl = self._pnl_by_period(trades, period="year")
        print(f"   → Monthly P&L periods: {len(monthly_pnl)}")
        print(f"   → Yearly P&L periods: {len(yearly_pnl)}")
        print("   ✓ P&L breakdown complete")
        
        print("4. Analyzing symbol performance...")
        from tests.backtest.utils.result_analyzer import ResultAnalyzer

        symbol_performance = ResultAnalyzer.analyze_by_symbol(trades)
        print(f"   → Symbols traded: {len(symbol_performance)}")
        print("   ✓ Symbol analysis complete")
        
        print("5. Risk analysis...")
        risk_analysis = self._risk_analysis(trades)
        print(f"   → Trades checked: {risk_analysis['trades_checked']}")
        print(f"   → Max single-trade leverage: {risk_analysis['max_single_trade_leverage']:.2f}x")
        print("   ✓ Risk analysis complete")
        
        print("6. Generating reports...")
        output_dir = PROJECT_ROOT / "data"
        output_dir.mkdir(exist_ok=True)
        
        reports = {
            "equity_curve": {
                "description": "Daily equity progression from start to end of backtest",
                "data_points": len(equity_curve),
                "data": equity_curve,
                "drawdown": drawdown,
            },
            "monthly_pnl": {
                "description": "Monthly profit/loss breakdown",
                "months": len(monthly_pnl),
                "data": monthly_pnl,
            },
            "yearly_pnl": {
                "description": "Yearly profit/loss breakdown",
                "years": len(yearly_pnl),
                "data": yearly_pnl,
            },
            "symbol_performance": {
                "description": "Per-symbol win rate, total P&L, trade count",
                "symbols": len(symbol_performance),
                "data": symbol_performance,
            },
            "risk_analysis": {
                "description": "Risk budget utilization over time",
                "trades": len(trades),
                "data": risk_analysis,
            },
        }
        analysis = {
            "status": "ANALYSIS COMPLETE",
            "run_id": phase3.get("run_id") or self.config.run_id,
            "summary_metrics": phase3.get("summary_metrics", {}),
            "reports": reports,
            "output_directory": str(output_dir),
            "timestamp": datetime.now().isoformat(),
        }
        analysis_file = output_dir / f"backtest_{self.config.run_id}_analysis.json"
        with open(analysis_file, "w") as f:
            json.dump(analysis, f, indent=2, default=str)
        
        print(f"   → Output location: {output_dir}")
        print(f"   → File: {analysis_file.name}")
        
        print("\n✓ Phase 4 Summary:")
        print("  → Equity curve generated")
        print("  → P&L breakdown complete")
        print("  → Symbol performance analyzed")
        print("  → Risk timeline computed")
        print("  → All reports ready")
        print(f"  → Status: ANALYSIS COMPLETE")
        
        analysis["analysis_file"] = str(analysis_file)
        return analysis

    def _build_equity_curve(self, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
        equity = float(self.config.starting_equity)
        curve = [{"date": self.config.start_date, "equity": round(equity, 2)}]
        for trade in sorted(trades, key=lambda item: str(item.get("exit_timestamp", ""))):
            equity += float(trade.get("pnl", 0.0) or 0.0)
            exit_date = str(trade.get("exit_session_date") or str(trade.get("exit_timestamp", ""))[:10])
            curve.append({"date": exit_date, "equity": round(equity, 2)})
        return curve

    @staticmethod
    def _calculate_drawdown(equity_curve: list[dict[str, Any]]) -> dict[str, float]:
        peak = 0.0
        max_drawdown = 0.0
        max_drawdown_pct = 0.0
        for point in equity_curve:
            equity = float(point.get("equity", 0.0) or 0.0)
            peak = max(peak, equity)
            drawdown = peak - equity
            drawdown_pct = drawdown / peak if peak > 0 else 0.0
            max_drawdown = max(max_drawdown, drawdown)
            max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
        return {"max_drawdown": round(max_drawdown, 2), "max_drawdown_pct": round(max_drawdown_pct, 6)}

    @staticmethod
    def _pnl_by_period(trades: list[dict[str, Any]], period: str) -> dict[str, float]:
        result: dict[str, float] = {}
        for trade in trades:
            exit_date = str(trade.get("exit_session_date") or str(trade.get("exit_timestamp", ""))[:10])
            key = exit_date[:7] if period == "month" else exit_date[:4]
            if not key:
                key = "unknown"
            result[key] = round(result.get(key, 0.0) + float(trade.get("pnl", 0.0) or 0.0), 2)
        return dict(sorted(result.items()))

    def _risk_analysis(self, trades: list[dict[str, Any]]) -> dict[str, Any]:
        max_leverage = 0.0
        max_entry_risk_pct = 0.0
        for trade in trades:
            equity = float(trade.get("entry_equity_before", self.config.starting_equity) or self.config.starting_equity)
            entry = float(trade.get("entry_price", 0.0) or 0.0)
            stop = float(trade.get("initial_stop", 0.0) or 0.0)
            shares = float(trade.get("shares", 0.0) or 0.0)
            position_cost = float(trade.get("position_cost", entry * shares) or 0.0)
            if equity <= 0:
                continue
            max_leverage = max(max_leverage, position_cost / equity)
            max_entry_risk_pct = max(max_entry_risk_pct, abs(entry - stop) * shares / equity)
        return {
            "trades_checked": len(trades),
            "max_single_trade_leverage": round(max_leverage, 4),
            "max_entry_risk_pct": round(max_entry_risk_pct, 6),
            "per_trade_risk_limit_pct": self.config.risk_per_trade_pct,
            "open_risk_limit_pct": self.config.max_open_risk_pct,
            "leverage_limit": self.config.leverage_ratio,
        }
