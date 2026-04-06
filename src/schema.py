from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppMetaConfig:
    name: str
    timezone: str


@dataclass(frozen=True)
class AlpacaHistoryConfig:
    weekly_weeks: int
    daily_days: int
    hourly_hours: int


@dataclass(frozen=True)
class AlpacaRateLimitConfig:
    max_requests_per_minute: int


@dataclass(frozen=True)
class AlpacaCacheConfig:
    enabled: bool
    overlap_bars: int


@dataclass(frozen=True)
class AlpacaConfig:
    api_key_id: str
    api_secret_key: str
    market_data_base_url: str
    trading_base_url: str
    timeout_seconds: int
    retry_attempts: int
    retry_sleep_seconds: int
    rate_limit_sleep_seconds: int
    adjustment: str
    feed: str
    history: AlpacaHistoryConfig
    rate_limit: AlpacaRateLimitConfig
    cache: AlpacaCacheConfig


@dataclass(frozen=True)
class EarningsCalendarConfig:
    enabled: bool
    provider: str
    base_url: str
    api_key: str | None
    horizon: str
    timeout_seconds: int


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
    trigger_mode: str
    atr_period: int


@dataclass(frozen=True)
class StrategyConfig:
    weekly: WeeklyStrategyConfig
    daily: DailyStrategyConfig
    hourly: HourlyStrategyConfig


@dataclass(frozen=True)
class QualificationConfig:
    minimum_reward_risk: float
    intraday_minimum_reward_risk: float
    strong_divergence_exhaustion_multiplier: float
    earnings_block_days_before: int
    earnings_block_days_after: int
    earnings_warn_days_before: int


@dataclass(frozen=True)
class TradePlanConfig:
    safezone_lookback: int
    safezone_coefficient: float
    thermometer_period: int
    thermometer_target_multiplier: float


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool
    bot_token: str | None
    chat_id: str | None


@dataclass(frozen=True)
class AlertConfig:
    cooldown_hours: int
    qualified_display_limit: int
    max_triggered_signals_per_scan: int
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
    alpaca: AlpacaConfig
    earnings_calendar: EarningsCalendarConfig
    universe: UniverseConfig
    strategy: StrategyConfig
    qualification: QualificationConfig
    trade_plan: TradePlanConfig
    alerts: AlertConfig
    market_filter: MarketFilterConfig
    runtime: RuntimeConfig
    storage: StorageConfig
