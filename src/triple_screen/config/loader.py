from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from triple_screen.config.schema import (
    AlpacaCacheConfig,
    AlpacaConfig,
    AlpacaHistoryConfig,
    AlpacaRateLimitConfig,
    AlertConfig,
    AppConfig,
    AppMetaConfig,
    DailyStrategyConfig,
    HourlyStrategyConfig,
    MarketFilterConfig,
    RiskConfig,
    RuntimeConfig,
    StorageConfig,
    StrategyConfig,
    TelegramConfig,
    UniverseConfig,
    WeeklyStrategyConfig,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"


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
    alpaca_raw = raw.get("data_source", {}).get("alpaca", {})
    alpaca_history_raw = alpaca_raw.get("history", {})
    alpaca_rate_limit_raw = alpaca_raw.get("rate_limit", {})
    alpaca_cache_raw = alpaca_raw.get("cache", {})
    universe_raw = raw.get("universe", {})
    strategy_raw = raw.get("strategy", {})
    weekly_raw = strategy_raw.get("weekly", {})
    daily_raw = strategy_raw.get("daily", {})
    hourly_raw = strategy_raw.get("hourly", {})
    risk_raw = raw.get("risk", {})
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
        alpaca=AlpacaConfig(
            api_key_id=_require_env(alpaca_raw["api_key_id_env"]),
            api_secret_key=_require_env(alpaca_raw["api_secret_key_env"]),
            market_data_base_url=alpaca_raw.get("market_data_base_url", "https://data.alpaca.markets"),
            trading_base_url=alpaca_raw.get("trading_base_url", "https://api.alpaca.markets"),
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
        universe=UniverseConfig(
            mode=universe_raw.get("mode", "market_cap_top"),
            top_n=int(universe_raw.get("top_n", 300)),
            static_file=_resolve_path(PROJECT_ROOT, universe_raw["static_file"]) if universe_raw.get("static_file") else None,
            symbols=_load_universe_symbols(PROJECT_ROOT, universe_raw.get("static_file")),
            custom_symbols=list(universe_raw.get("custom_symbols", [])),
            allowed_ticker_types=list(universe_raw.get("allowed_ticker_types", ["CS"])),
            exclude_symbols_containing=list(universe_raw.get("exclude_symbols_containing", ["."])),
        ),
        strategy=StrategyConfig(
            weekly=WeeklyStrategyConfig(
                macd_fast=int(weekly_raw.get("macd_fast", 12)),
                macd_slow=int(weekly_raw.get("macd_slow", 26)),
                macd_signal=int(weekly_raw.get("macd_signal", 9)),
                confirm_bars=int(weekly_raw.get("confirm_bars", 2)),
            ),
            daily=DailyStrategyConfig(
                rsi_period=int(daily_raw.get("rsi_period", 14)),
                rsi_oversold=float(daily_raw.get("rsi_oversold", 35)),
                rsi_overbought=float(daily_raw.get("rsi_overbought", 65)),
                recovery_mode=bool(daily_raw.get("recovery_mode", True)),
            ),
            hourly=HourlyStrategyConfig(
                breakout_bars=int(hourly_raw.get("breakout_bars", 6)),
                atr_period=int(hourly_raw.get("atr_period", 14)),
            ),
        ),
        risk=RiskConfig(
            account_size=float(risk_raw.get("account_size", 100000)),
            account_risk_pct=float(risk_raw.get("account_risk_pct", 0.01)),
            atr_multiplier=float(risk_raw.get("atr_multiplier", 1.5)),
            reward_risk_ratio=float(risk_raw.get("reward_risk_ratio", 2.0)),
            max_hold_bars=int(risk_raw.get("max_hold_bars", 72)),
        ),
        alerts=AlertConfig(
            cooldown_hours=int(alerts_raw.get("cooldown_hours", 6)),
            max_signals_per_scan=int(alerts_raw.get("max_signals_per_scan", 10)),
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
