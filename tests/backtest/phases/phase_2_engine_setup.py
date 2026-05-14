"""Phase 2: Backtest Engine Setup."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class EngineSetup:
    """Validates and prepares backtest engine."""

    def __init__(self, config: Any) -> None:
        self.config = config
        
    def execute(self) -> dict[str, Any]:
        """Execute Phase 2: Engine validation and setup."""
        from datetime import datetime
        
        print("\n1. Loading strategy configuration...")
        try:
            from config.loader import load_settings
            settings = load_settings()
            print("   ✓ Settings loaded")
        except Exception as e:
            print(f"   ⚠ Warning: {e}")
            settings = None
        
        print("2. Validating model...")
        try:
            import trading_models
            model = trading_models.get_model(self.config.model_name)
            print(f"   ✓ Model '{self.config.model_name}' loaded")
        except Exception as e:
            print(f"   ✗ Model load failed: {e}")
            return {"status": "FAILED", "error": str(e)}
        
        print("3. Checking indicators availability...")
        try:
            import indicators
            required_indicators = [
                "calc_ema",
                "calc_force_index_ema",
                "calc_atr_stops",
                "calc_safezone_stop",
                "calc_nick_stop",
            ]
            missing = []
            for indicator_name in required_indicators:
                if hasattr(indicators, indicator_name):
                    print(f"   ✓ {indicator_name} available")
                else:
                    missing.append(indicator_name)
                    print(f"   ✗ {indicator_name} MISSING")
            
            if missing:
                raise ValueError(f"Missing indicators: {missing}")
        except Exception as e:
            print(f"   ✗ Indicator check failed: {e}")
            return {"status": "FAILED", "error": str(e)}
        
        print("4. Validating backtest parameters...")
        params_validation = {
            "starting_equity": self.config.starting_equity,
            "risk_per_trade": self.config.risk_per_trade_pct,
            "max_open_risk": self.config.max_open_risk_pct,
            "leverage": self.config.leverage_ratio,
            "model": self.config.model_name,
        }
        
        try:
            # Validate ranges
            assert self.config.starting_equity > 0, "Starting equity must be positive"
            assert 0 < self.config.risk_per_trade_pct < 0.1, "Risk per trade should be 1-10%"
            assert 0 < self.config.max_open_risk_pct < 0.2, "Max open risk should be 1-20%"
            assert self.config.leverage_ratio >= 1.0, "Leverage must be >= 1.0"
            print("   ✓ All parameters valid")
        except AssertionError as e:
            print(f"   ✗ Parameter validation failed: {e}")
            return {"status": "FAILED", "error": str(e)}
        
        print("5. Checking database schema...")
        try:
            from storage.sqlite import SQLiteStorage
            database_path = settings.storage.database_path if settings is not None else PROJECT_ROOT / "data" / "triple_screen.db"
            db = SQLiteStorage(database_path)
            print(f"   ✓ Database available: {database_path}")
        except Exception as e:
            print(f"   ⚠ Database warning: {e}")
        
        print("\n✓ Phase 2 Summary:")
        print(f"  → Model: {self.config.model_name}")
        print(f"  → Account: ${self.config.starting_equity:,.0f}")
        print(f"  → Risk per trade: {self.config.risk_per_trade_pct*100:.1f}%")
        print(f"  → Max open risk: {self.config.max_open_risk_pct*100:.1f}%")
        print(f"  → Leverage: {self.config.leverage_ratio}x")
        print(f"  → Status: READY")
        
        return {
            "model": self.config.model_name,
            "parameters_validation": params_validation,
            "status": "READY",
            "timestamp": datetime.now().isoformat(),
        }
