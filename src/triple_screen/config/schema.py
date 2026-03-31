from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppMetaConfig:
    name: str
    timezone: str


@dataclass(frozen=True)
class PolygonHistoryConfig:
    weekly_weeks: int
    daily_days: int
    hourly_hours: int


@dataclass(frozen=True)
class PolygonRateLimitConfig:
    max_requests_per_minute: int


@dataclass(frozen=True)
class PolygonCacheConfig:
    enabled: bool
    overlap_bars: int


@dataclass(frozen=True)
class PolygonConfig:
    api_key: str
    base_url: str
    timeout_seconds: int
    retry_attempts: int
    retry_sleep_seconds: int
    rate_limit_sleep_seconds: int
    adjusted: bool
    history: PolygonHistoryConfig
    rate_limit: PolygonRateLimitConfig
    cache: PolygonCacheConfig


@dataclass(frozen=True)
class UniverseConfig:
    mode: str
    top_n: int
    static_file: Path | None
    symbols: list[dict]
    custom_symbols: list[str]
    allowed_ticker_types: list[str]
    exclude_symbols_containing: list[str]


@dataclass(frozen=True)
class WeeklyStrategyConfig:
    macd_fast: int
    macd_slow: int
    macd_signal: int
    confirm_bars: int


@dataclass(frozen=True)
class DailyStrategyConfig:
    rsi_period: int
    rsi_oversold: float
    rsi_overbought: float
    recovery_mode: bool


@dataclass(frozen=True)
class HourlyStrategyConfig:
    breakout_bars: int
    atr_period: int


@dataclass(frozen=True)
class StrategyConfig:
    weekly: WeeklyStrategyConfig
    daily: DailyStrategyConfig
    hourly: HourlyStrategyConfig


@dataclass(frozen=True)
class RiskConfig:
    account_size: float
    account_risk_pct: float
    atr_multiplier: float
    reward_risk_ratio: float
    max_hold_bars: int


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool
    bot_token: str | None
    chat_id: str | None


@dataclass(frozen=True)
class AlertConfig:
    cooldown_hours: int
    max_signals_per_scan: int
    telegram: TelegramConfig


@dataclass(frozen=True)
class MarketFilterConfig:
    enabled: bool
    benchmark_symbol: str


@dataclass(frozen=True)
class RuntimeConfig:
    scan_interval_minutes: int
    max_workers: int
    log_level: str
    log_file: Path


@dataclass(frozen=True)
class StorageConfig:
    database_path: Path


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    config_path: Path
    app: AppMetaConfig
    polygon: PolygonConfig
    universe: UniverseConfig
    strategy: StrategyConfig
    risk: RiskConfig
    alerts: AlertConfig
    market_filter: MarketFilterConfig
    runtime: RuntimeConfig
    storage: StorageConfig
