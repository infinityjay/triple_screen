"""Backtest fixtures and config loading."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def load_backtest_config(config_path: str) -> Any:
    """Load backtest configuration from YAML."""
    import yaml
    from tests.backtest.backtest_runner import BacktestConfig
    
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_file) as f:
        config_dict = yaml.safe_load(f)

    output_config = config_dict.get("output", {})
    data_config = config_dict.get("data", {})
    universe_config = config_dict.get("universe", {})
    
    # Convert to BacktestConfig object
    return BacktestConfig(
        starting_equity=config_dict.get("account", {}).get("starting_equity", 100000),
        risk_per_trade_pct=config_dict.get("risk", {}).get("per_trade_pct", 0.02),
        max_open_risk_pct=config_dict.get("risk", {}).get("max_open_pct", 0.06),
        leverage_ratio=config_dict.get("leverage", {}).get("ratio", 1.5),
        start_date=data_config.get("start_date", "2023-01-01"),
        end_date=data_config.get("end_date", "2026-05-01"),
        model_name=config_dict.get("model", {}).get("active", "elder_force"),
        universe=universe_config.get("symbols_file", "config/universe_us_top300.yaml"),
        symbols=universe_config.get("symbols"),
        sqlite_only=bool(data_config.get("sqlite_only", True)),
        max_symbols=int(universe_config.get("max_symbols", 0) or 0),
        include_trade_details=bool(output_config.get("include_trade_details", True)),
    )


def create_sample_config(output_path: str | None = None) -> str:
    """Create a sample backtest config file."""
    import yaml
    
    sample_config = {
        "account": {
            "starting_equity": 100000.0,
        },
        "risk": {
            "per_trade_pct": 0.02,
            "max_open_pct": 0.06,
        },
        "leverage": {
            "mode": "1.5x_margin",
            "ratio": 1.5,
        },
        "model": {
            "active": "elder_force",
        },
        "universe": {
            "symbols_file": "config/universe_us_top300.yaml",
        },
        "data": {
            "start_date": "2023-01-01",
            "end_date": "2026-05-01",
            "timeframes": ["daily", "weekly"],
            "source": "alpaca",
        },
        "entry": {
            "methods": ["ema_penetration", "previous_day_breakout"],
            "confirmation": "hourly",
        },
        "stops": {
            "method": "atr_1x",
            "trailing_rule": "monotonic_ratchet",
        },
        "execution": {
            "commission_per_trade": 1.0,
            "slippage": 0.0,
        },
        "validation": {
            "spot_check_count": 20,
            "compare_to_legacy": True,
            "min_acceptable_drawdown_pct": -20.0,
        },
        "output": {
            "format": "json",
            "include_daily_metrics": False,
            "include_trade_details": True,
        },
    }
    
    if output_path is None:
        output_path = str(PROJECT_ROOT / "tests" / "backtest" / "backtest_config_sample.yaml")
    
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, "w") as f:
        yaml.dump(sample_config, f, default_flow_style=False, sort_keys=False)
    
    return str(output_file)
