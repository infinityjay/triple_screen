from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from config.schema import (
    AlpacaCacheConfig,
    AlpacaConfig,
    AlpacaHistoryConfig,
    AlpacaRateLimitConfig,
    AlertConfig,
    AppConfig,
    AppMetaConfig,
    ServerConfig,
    DailyStrategyConfig,
    EarningsCalendarConfig,
    HourlyStrategyConfig,
    MarketFilterConfig,
    QualificationConfig,
    RuntimeConfig,
    StorageConfig,
    StrategyConfig,
    TelegramConfig,
    TradePlanConfig,
    TradingModelConfig,
    UniverseConfig,
    WeeklyStrategyConfig,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def _validate_trigger_mode(raw_value: str | None) -> str:
    trigger_mode = (raw_value or "trailing_bar").strip().lower()
    if trigger_mode != "trailing_bar":
        raise ValueError(f"Unsupported hourly trigger_mode: {trigger_mode}")
    return trigger_mode


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return project_root / path


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _env_or_default(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value else default


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _load_universe_symbols(project_root: Path, raw_path: str | None) -> list[dict]:
    if not raw_path:
        return []
    universe_path = _resolve_path(project_root, raw_path)
    payload = _load_yaml(universe_path)
    return list(payload.get("symbols", []))


def load_settings(config_path: str | Path | None = None) -> AppConfig:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    resolved_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not resolved_path.is_absolute():
        resolved_path = PROJECT_ROOT / resolved_path

    raw = _load_yaml(resolved_path)

    app_raw = raw.get("app", {})
    server_raw = raw.get("server", {})
    alpaca_raw = raw.get("data_source", {}).get("alpaca", {})
    alpaca_history_raw = alpaca_raw.get("history", {})
    alpaca_rate_limit_raw = alpaca_raw.get("rate_limit", {})
    alpaca_cache_raw = alpaca_raw.get("cache", {})
    earnings_raw = raw.get("data_source", {}).get("earnings_calendar", {})
    universe_raw = raw.get("universe", {})
    trading_model_raw = raw.get("trading_model", {})
    strategy_raw = raw.get("strategy", {})
    weekly_raw = strategy_raw.get("weekly", {})
    daily_raw = strategy_raw.get("daily", {})
    hourly_raw = strategy_raw.get("hourly", {})
    qualification_raw = strategy_raw.get("qualification", {})
    trade_plan_raw = raw.get("trade_plan", raw.get("risk", {}))
    alerts_raw = raw.get("alerts", {})
    telegram_raw = alerts_raw.get("telegram", {})
    market_filter_raw = raw.get("market_filter", {})
    runtime_raw = raw.get("runtime", {})
    storage_raw = raw.get("storage", {})

    telegram_enabled = bool(telegram_raw.get("enabled", False))
    telegram = TelegramConfig(
        enabled=telegram_enabled,
        bot_token=_require_env(telegram_raw["bot_token_env"]) if telegram_enabled else None,
        chat_id=_require_env(telegram_raw["chat_id_env"]) if telegram_enabled else None,
    )

    return AppConfig(
        project_root=PROJECT_ROOT,
        config_path=resolved_path,
        app=AppMetaConfig(
            name=app_raw.get("name", "Triple Screen Scanner"),
            timezone=app_raw.get("timezone", "UTC"),
        ),
        server=ServerConfig(
            host=server_raw.get("host", "127.0.0.1"),
            port=int(server_raw.get("port", 8100)),
        ),
        alpaca=AlpacaConfig(
            api_key_id=_require_env(alpaca_raw["api_key_id_env"]),
            api_secret_key=_require_env(alpaca_raw["api_secret_key_env"]),
            market_data_base_url=_env_or_default(
                "ALPACA_MARKET_DATA_BASE_URL",
                alpaca_raw.get("market_data_base_url", "https://data.alpaca.markets/v2"),
            ),
            trading_base_url=_env_or_default(
                "ALPACA_TRADING_BASE_URL",
                alpaca_raw.get("trading_base_url", "https://paper-api.alpaca.markets/v2"),
            ),
            timeout_seconds=int(alpaca_raw.get("timeout_seconds", 15)),
            retry_attempts=int(alpaca_raw.get("retry_attempts", 3)),
            retry_sleep_seconds=int(alpaca_raw.get("retry_sleep_seconds", 5)),
            rate_limit_sleep_seconds=int(alpaca_raw.get("rate_limit_sleep_seconds", 60)),
            adjustment=alpaca_raw.get("adjustment", "split"),
            feed=alpaca_raw.get("feed", "iex"),
            history=AlpacaHistoryConfig(
                weekly_weeks=int(alpaca_history_raw.get("weekly_weeks", 60)),
                daily_days=int(alpaca_history_raw.get("daily_days", 90)),
                hourly_hours=int(alpaca_history_raw.get("hourly_hours", 160)),
            ),
            rate_limit=AlpacaRateLimitConfig(
                max_requests_per_minute=int(alpaca_rate_limit_raw.get("max_requests_per_minute", 180)),
            ),
            cache=AlpacaCacheConfig(
                enabled=bool(alpaca_cache_raw.get("enabled", True)),
                overlap_bars=int(alpaca_cache_raw.get("overlap_bars", 3)),
            ),
        ),
        earnings_calendar=EarningsCalendarConfig(
            enabled=bool(earnings_raw.get("enabled", False)),
            provider=(earnings_raw.get("provider", "alphavantage") or "alphavantage").strip().lower(),
            base_url=earnings_raw.get("base_url", "https://www.alphavantage.co/query"),
            api_key=_require_env(earnings_raw["api_key_env"])
            if earnings_raw.get("enabled", False) and earnings_raw.get("api_key_env")
            else None,
            horizon=earnings_raw.get("horizon", "3month"),
            timeout_seconds=int(earnings_raw.get("timeout_seconds", 20)),
        ),
        universe=UniverseConfig(
            mode=universe_raw.get("mode", "market_cap_top"),
            top_n=int(universe_raw.get("top_n", 300)),
            static_file=_resolve_path(PROJECT_ROOT, universe_raw["static_file"]) if universe_raw.get("static_file") else None,
            symbols=_load_universe_symbols(PROJECT_ROOT, universe_raw.get("static_file")),
            custom_symbols=list(universe_raw.get("custom_symbols", [])),
            allowed_ticker_types=list(universe_raw.get("allowed_ticker_types", ["CS"])),
            exclude_symbols_containing=list(universe_raw.get("exclude_symbols_containing", ["."])),
        ),
        trading_model=TradingModelConfig(
            active=str(trading_model_raw.get("active", "legacy_pre_45c9b2d")),
        ),
        strategy=StrategyConfig(
            weekly=WeeklyStrategyConfig(
                macd_fast=int(weekly_raw.get("macd_fast", 12)),
                macd_slow=int(weekly_raw.get("macd_slow", 26)),
                macd_signal=int(weekly_raw.get("macd_signal", 9)),
                confirm_bars=int(weekly_raw.get("confirm_bars", 2)),
                require_impulse_alignment=bool(weekly_raw.get("require_impulse_alignment", True)),
            ),
            daily=DailyStrategyConfig(
                rsi_period=int(daily_raw.get("rsi_period", 14)),
                rsi_oversold=float(daily_raw.get("rsi_oversold", 35)),
                rsi_overbought=float(daily_raw.get("rsi_overbought", 65)),
                recovery_mode=bool(daily_raw.get("recovery_mode", True)),
                value_band_atr_multiplier=float(daily_raw.get("value_band_atr_multiplier", 0.75)),
            ),
            hourly=HourlyStrategyConfig(
                trigger_mode=_validate_trigger_mode(hourly_raw.get("trigger_mode")),
                atr_period=int(hourly_raw.get("atr_period", 14)),
            ),
        ),
        qualification=QualificationConfig(
            minimum_reward_risk=float(qualification_raw.get("minimum_reward_risk", 1.2)),
            intraday_minimum_reward_risk=float(qualification_raw.get("intraday_minimum_reward_risk", 1.5)),
            strong_divergence_exhaustion_multiplier=float(
                qualification_raw.get("strong_divergence_exhaustion_multiplier", 2.0)
            ),
            earnings_block_days_before=int(qualification_raw.get("earnings_block_days_before", 2)),
            earnings_block_days_after=int(qualification_raw.get("earnings_block_days_after", 1)),
            earnings_warn_days_before=int(qualification_raw.get("earnings_warn_days_before", 5)),
        ),
        trade_plan=TradePlanConfig(
            safezone_lookback=int(trade_plan_raw.get("safezone_lookback", 10)),
            safezone_ema_period=int(trade_plan_raw.get("safezone_ema_period", 22)),
            safezone_long_coefficient=float(
                trade_plan_raw.get("safezone_long_coefficient", trade_plan_raw.get("safezone_coefficient", 2.0))
            ),
            safezone_short_coefficient=float(trade_plan_raw.get("safezone_short_coefficient", 3.0)),
            thermometer_period=int(trade_plan_raw.get("thermometer_period", 22)),
            thermometer_target_multiplier=float(trade_plan_raw.get("thermometer_target_multiplier", 1.0)),
        ),
        alerts=AlertConfig(
            cooldown_hours=int(alerts_raw.get("cooldown_hours", 6)),
            qualified_display_limit=int(
                alerts_raw.get("qualified_display_limit", alerts_raw.get("max_signals_per_scan", 15))
            ),
            telegram=telegram,
        ),
        market_filter=MarketFilterConfig(
            enabled=bool(market_filter_raw.get("enabled", True)),
            benchmark_symbol=market_filter_raw.get("benchmark_symbol", "SPY"),
        ),
        runtime=RuntimeConfig(
            scan_interval_minutes=int(runtime_raw.get("scan_interval_minutes", 60)),
            max_workers=int(runtime_raw.get("max_workers", 5)),
            log_level=runtime_raw.get("log_level", "INFO"),
            log_file=_resolve_path(PROJECT_ROOT, runtime_raw.get("log_file", "logs/scanner.log")),
        ),
        storage=StorageConfig(
            database_path=_resolve_path(PROJECT_ROOT, storage_raw.get("database_path", "data/triple_screen.db")),
        ),
    )
