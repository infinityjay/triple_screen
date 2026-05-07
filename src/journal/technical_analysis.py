from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

import indicators
import trading_models
from clients.alpaca import AlpacaClient
from config.loader import load_settings
from storage.sqlite import SQLiteStorage


class TechnicalAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIProviderConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.model)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_round(value: Any, digits: int = 4) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _format_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)).date().isoformat()
    except ValueError:
        text = str(value)
        return text[:10] if text else None


def _metric(label: str, value: Any, emphasis: str = "neutral") -> dict[str, Any]:
    return {"label": label, "value": "—" if value is None or value == "" else str(value), "emphasis": emphasis}


def _check(label: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"label": label, "pass": bool(passed), "detail": detail}


def _normalize_symbol(value: str) -> str:
    return re.sub(r"[^A-Z0-9.\-]", "", str(value or "").strip().upper())


def _daily_state_label(state: str | None) -> str:
    labels = {
        "NEUTRAL": "Neutral",
        "NO_PULLBACK": "Weekly long, but daily pullback is not clear yet",
        "NO_RALLY": "Weekly short, but daily rally is not clear yet",
        "STRUCTURE_BROKEN": "Structure broken",
        "ACCELERATING_PULLBACK": "Pullback is still accelerating",
        "ACCELERATING_RALLY": "Rally is still accelerating",
        "PULLBACK_WAIT_VALUE_BAND": "Pullback appeared; waiting for return to the 13EMA value band",
        "RALLY_WAIT_VALUE_BAND": "Rally appeared; waiting for return to the 13EMA value band",
        "PULLBACK_WAIT_HISTOGRAM": "Returned to 13EMA value band; waiting for histogram to turn up",
        "RALLY_WAIT_HISTOGRAM": "Returned to 13EMA value band; waiting for histogram to turn down",
        "PULLBACK_HISTOGRAM_TURNED": "Histogram turned up after pullback to value band",
        "RALLY_HISTOGRAM_TURNED": "Histogram turned down after rally to value band",
        "PULLBACK_WAIT_FORCE_BELOW_ZERO": "Waiting for 2-day Force EMA to fall below 0",
        "PULLBACK_FORCE_READY_WAIT_IMPULSE": "Force is ready; waiting for daily impulse system to stop opposing the trade",
        "PULLBACK_FORCE_BELOW_ZERO": "2-day Force EMA fell below 0",
        "RALLY_WAIT_FORCE_ABOVE_ZERO": "Waiting for 2-day Force EMA to rise above 0",
        "RALLY_FORCE_READY_WAIT_IMPULSE": "Force is ready; waiting for daily impulse system to stop opposing the trade",
        "RALLY_FORCE_ABOVE_ZERO": "2-day Force EMA rose above 0",
    }
    return labels.get(str(state or ""), str(state or "—"))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _ai_provider_config() -> AIProviderConfig:
    return AIProviderConfig(
        base_url=(os.getenv("TECH_ANALYSIS_AI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
        api_key=(os.getenv("TECH_ANALYSIS_AI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip(),
        model=(os.getenv("TECH_ANALYSIS_AI_MODEL") or os.getenv("OPENAI_MODEL") or "").strip(),
        timeout_seconds=max(int(os.getenv("TECH_ANALYSIS_AI_TIMEOUT_SECONDS") or "30"), 5),
    )


def _build_market_client() -> tuple[Any, AlpacaClient]:
    # The single-symbol analysis page does not rely on Telegram or earnings APIs,
    # so we provide harmless placeholders when those optional env vars are absent.
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "disabled")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "disabled")
    os.environ.setdefault("ALPHAVANTAGE_API_KEY", "disabled")

    settings = load_settings()
    storage = SQLiteStorage(settings.storage.database_path)
    storage.init_db()
    market_data = AlpacaClient(
        config=settings.alpaca,
        storage=storage,
        market_timezone=settings.app.timezone,
    )
    return settings, market_data


def _build_divergence_snapshot(settings: Any, weekly_frame: Any, daily_frame: Any, direction: str) -> dict[str, Any]:
    if direction not in {"LONG", "SHORT"}:
        neutral_note = {
            "detected": False,
            "strong_alert": False,
            "timeframe": "Weekly/Daily",
            "direction": direction,
            "reason": "Weekly direction is unclear; trend divergence is not evaluated independently.",
        }
        return {"weekly": neutral_note, "daily": neutral_note}

    return {
        "weekly": indicators.detect_divergence(
            weekly_frame,
            settings.strategy,
            direction,
            "Weekly",
            settings.qualification.strong_divergence_exhaustion_multiplier,
        ),
        "daily": indicators.detect_divergence(
            daily_frame,
            settings.strategy,
            direction,
            "Daily",
            settings.qualification.strong_divergence_exhaustion_multiplier,
        ),
    }


def _build_followup_decision(weekly: dict[str, Any], daily: dict[str, Any], divergence: dict[str, Any]) -> dict[str, str]:
    strong_divergence = bool(
        divergence.get("weekly", {}).get("strong_alert") or divergence.get("daily", {}).get("strong_alert")
    )

    if not weekly.get("actionable"):
        return {
            "code": "NO_TREND",
            "label": "Do Not Track Yet",
            "tone": "warn",
            "reason": "Weekly histogram direction is unclear; there is no stable trend thesis yet.",
        }

    if weekly.get("pass") and daily.get("pass") and not strong_divergence:
        return {
            "code": "READY",
            "label": "Priority Watch",
            "tone": "safe",
            "reason": "Weekly trend is confirmed and daily setup is ready; this deserves priority monitoring.",
        }

    if weekly.get("pass") and daily.get("pass") and strong_divergence:
        return {
            "code": "READY_WITH_CAUTION",
            "label": "Watch, But More Conservatively",
            "tone": "warn",
            "reason": "Weekly and daily conditions pass, but divergence warns of possible trend exhaustion; reduce execution aggressiveness.",
        }

    if weekly.get("pass") and daily.get("watch"):
        return {
            "code": "WATCH",
            "label": "Continue Watching",
            "tone": "info",
            "reason": "Weekly direction is mostly clear, but daily Force Index or daily impulse system is not fully ready.",
        }

    if weekly.get("actionable") and not weekly.get("pass"):
        return {
            "code": "EARLY_TREND",
            "label": "Track First, No Rush",
            "tone": "info",
            "reason": "Weekly direction appeared, but confirmation bars or trend-side position are not complete enough.",
        }

    return {
        "code": "REJECT",
        "label": "Do Not Track Yet",
        "tone": "warn",
        "reason": daily.get("reject_reason") or "Daily setup quality is insufficient; do not allocate more attention yet.",
    }


def _build_system_analysis(symbol: str, model_id: str | None = None) -> dict[str, Any]:
    settings, market_data = _build_market_client()
    model = trading_models.get_model(model_id or settings.trading_model.active)
    weekly_frame = market_data.get_weekly_bars(symbol)
    daily_frame = market_data.get_daily_bars(symbol)
    try:
        hourly_frame = market_data.get_hourly_bars(symbol)
    except Exception:
        hourly_frame = None

    weekly = model.screen_weekly(weekly_frame, settings.strategy)
    weekly_trend = str(weekly.get("trend") or "NEUTRAL")
    daily = model.screen_daily(daily_frame, weekly_trend, settings.strategy)
    divergence = _build_divergence_snapshot(settings, weekly_frame, daily_frame, weekly_trend)
    followup = _build_followup_decision(weekly, daily, divergence)

    safezone_stop = None
    safezone_noise = None
    nick_stop = None
    atr_stop_1x = None
    atr_stop_2x = None
    daily_atr = None
    stop_methods: list[dict[str, Any]] = []
    latest_temperature = None
    average_temperature = None
    latest_close = None
    latest_high = None
    latest_low = None
    latest_bar_at = None
    daily_ema13 = None
    entry_plan: dict[str, Any] = {}
    weekly_value_target: dict[str, Any] = {}
    execution_metrics: list[dict[str, Any]] = []
    execution_summary = "Execution levels are not provided when weekly direction is unclear."
    execution_hourly: dict[str, Any] = {}
    execution_exits: dict[str, Any] = {}
    suggested_entry = None
    suggested_stop = None
    suggested_target = None

    if daily_frame is not None and not daily_frame.empty:
        latest_row = daily_frame.iloc[-1]
        latest_close = _safe_round(latest_row.get("close"))
        latest_high = _safe_round(latest_row.get("high"))
        latest_low = _safe_round(latest_row.get("low"))
        latest_bar_at = _format_timestamp(daily_frame.index[-1])
        daily_ema_series = indicators.calc_ema(daily_frame["close"].astype(float), indicators.DAILY_EMA_PERIOD)
        daily_ema13 = _safe_round(daily_ema_series.iloc[-1])
        if weekly_trend in {"LONG", "SHORT"}:
            safezone_stop, safezone_noise = indicators.calc_safezone_stop(daily_frame, weekly_trend, settings.trade_plan)
            nick_stop = indicators.calc_nick_stop(daily_frame, weekly_trend)
            atr_stops, daily_atr_value = indicators.calc_atr_stops(
                daily_frame, weekly_trend, atr_period=settings.strategy.hourly.atr_period
            )
            atr_stop_1x = atr_stops.get(1.0)
            atr_stop_2x = atr_stops.get(2.0)
            daily_atr = _safe_round(daily_atr_value, 4)
            stop_methods = indicators.build_stop_methods(
                daily_frame,
                weekly_trend,
                settings.trade_plan,
                atr_period=settings.strategy.hourly.atr_period,
            )
            temperature, avg_temperature = indicators.calc_market_thermometer(
                daily_frame, settings.trade_plan.thermometer_period
            )
            latest_temperature = _safe_round(temperature.iloc[-1])
            average_temperature = _safe_round(avg_temperature.iloc[-1])
            entry_plan = indicators.calc_ema_penetration_entry_plan(daily_frame, weekly_trend) if model.use_planned_daily_entry else {}
            weekly_value_target = indicators.calc_weekly_value_target(weekly_frame, weekly_trend)

            planned_entry = entry_plan.get("ema_penetration_entry")
            if planned_entry is not None:
                execution_exits = model.calc_exits(
                    weekly_trend,
                    float(planned_entry),
                    daily_frame,
                    0.0,
                    settings.trade_plan,
                    weekly_frame=weekly_frame,
                )
                suggested_entry = _safe_round(execution_exits.get("entry"))
                suggested_stop = _safe_round(execution_exits.get("protective_stop_loss"))
                suggested_target = _safe_round(execution_exits.get("take_profit"))
                execution_summary = (
                    f"Current execution method: prioritize EMA penetration reference price {suggested_entry if suggested_entry is not None else '—'}; "
                    f"alternate trigger is one tick outside the previous-day high/low {entry_plan.get('breakout_entry', '—')}."
                    f" Initial stop still requires manual choice between SafeZone and Nick stop."
                )
                execution_metrics = [
                    _metric("EMA Penetration Reference", suggested_entry, "accent"),
                    _metric("Previous-Day Breakout Reference", _safe_round(entry_plan.get("breakout_entry")), "accent"),
                    _metric("Projected Next EMA", _safe_round(entry_plan.get("projected_next_ema"))),
                    _metric("Average Penetration", _safe_round(entry_plan.get("average_penetration"))),
                    _metric("SafeZone Initial Stop", _safe_round(execution_exits.get("initial_stop_safezone")), "warn"),
                    _metric("Nick Stop", _safe_round(execution_exits.get("initial_stop_nick")), "warn"),
                    _metric("Nick Reference Date", execution_exits.get("initial_stop_nick_reference_date")),
                    _metric("ATR 1x Trailing Stop", suggested_stop, "warn"),
                    _metric("ATR 2x Trailing Stop", _safe_round(execution_exits.get("stop_loss_atr_2x"))),
                    _metric("Weekly Value-Zone Target", suggested_target),
                    _metric(
                        "Weekly Value Zone",
                        (
                            f"{_safe_round(weekly_value_target.get('value_zone_low'))} ~ "
                            f"{_safe_round(weekly_value_target.get('value_zone_high'))}"
                        )
                        if weekly_value_target.get("available")
                        else "—",
                    ),
                    _metric("Internal Model Reward/Risk", _safe_round(execution_exits.get("reward_risk_ratio_model"), 2)),
                ]

    if weekly_trend in {"LONG", "SHORT"} and hourly_frame is not None and not hourly_frame.empty:
        intraday_plan = model.build_intraday_plan(
            direction=weekly_trend,
            daily_frame=daily_frame,
            weekly_frame=weekly_frame,
            hourly_frame=hourly_frame,
            settings=settings.strategy,
            trade_plan=settings.trade_plan,
            as_of=datetime.now(UTC),
        )
        execution_hourly = intraday_plan.hourly if intraday_plan else {}
        if execution_hourly.get("entry_price") is not None and daily_frame is not None and not daily_frame.empty and not execution_exits:
            execution_exits = intraday_plan.exits if intraday_plan else {}
            suggested_entry = _safe_round(execution_exits.get("entry"))
            suggested_stop = _safe_round(execution_exits.get("protective_stop_loss"))
            suggested_target = _safe_round(execution_exits.get("take_profit"))
            execution_summary = (
                f"Current execution method: watch trigger price {suggested_entry if suggested_entry is not None else '—'}, "
                f"Initial stop requires manual choice between SafeZone and Nick stop; "
                f"ATR 1x trailing stop reference {suggested_stop if suggested_stop is not None else '—'}."
                f" {execution_hourly.get('reason', '')}"
            )
            execution_metrics = [
                _metric("Suggested Entry Price", suggested_entry, "accent"),
                _metric("SafeZone Initial Stop", _safe_round(execution_exits.get("initial_stop_safezone")), "warn"),
                _metric("Nick Stop", _safe_round(execution_exits.get("initial_stop_nick")), "warn"),
                _metric("ATR 1x Trailing Stop", suggested_stop, "warn"),
                _metric("ATR 2x Trailing Stop", _safe_round(execution_exits.get("stop_loss_atr_2x"))),
                _metric("First Target", suggested_target),
                _metric("Hourly Status", execution_hourly.get("status")),
                _metric("Current Price", _safe_round(execution_hourly.get("close"))),
                _metric("Signal Bar High", _safe_round(execution_hourly.get("signal_bar_high"))),
                _metric("Signal Bar Low", _safe_round(execution_hourly.get("signal_bar_low"))),
                _metric("ATR", _safe_round(execution_hourly.get("atr"), 4)),
                _metric("Internal Model Reward/Risk", _safe_round(execution_exits.get("reward_risk_ratio_model"), 2)),
            ]
        else:
            execution_summary = execution_hourly.get("reason") or "Cannot generate execution levels right now."
            execution_metrics = [
                _metric("Suggested Buy Price", None),
                _metric("Current Protective Stop", None),
                _metric("Hourly Status", execution_hourly.get("status")),
            ]

    weekly_metrics = [
        _metric("Weekly Trend", weekly.get("trend")),
        _metric("Impulse Color", weekly.get("impulse_color"), "accent"),
        _metric("MACD", _safe_round(weekly.get("macd"), 6)),
        _metric("MACD Slope", _safe_round(weekly.get("macd_slope"), 6)),
        _metric("Signal", _safe_round(weekly.get("macd_signal"), 6)),
        _metric("Histogram", _safe_round(weekly.get("histogram"), 6)),
        _metric("Histogram Change", _safe_round(weekly.get("histogram_delta"), 6)),
        _metric("13EMA", _safe_round(weekly.get("ema13"), 4)),
        _metric("13EMA Slope", _safe_round(weekly.get("ema13_slope"), 6)),
        _metric("Confirmed Bars", f"{weekly.get('confirmed_bars', 0)} / {settings.strategy.weekly.confirm_bars}"),
        _metric("Trend Score", _safe_round(weekly.get("trend_score"), 2)),
    ]
    weekly_checks = [
        _check(
            "MACD Slope Direction",
            weekly.get("actionable", False),
            f"This week MACD slope {_safe_round(weekly.get('macd_slope'), 6)}, must be non-zero to define direction.",
        ),
        _check(
            "Consecutive Same-Direction MACD Bars",
            bool(weekly.get("pass_checks", {}).get("confirmed_bars")),
            f"Current {weekly.get('confirmed_bars', 0)} bars; rule requires at least {settings.strategy.weekly.confirm_bars} bars.",
        ),
        _check(
            "Impulse-System Block Rule",
            bool(weekly.get("pass_checks", {}).get("impulse_aligned")),
            (
                f"Impulse color {weekly.get('impulse_color', '—')}; long cannot be red and short cannot be green."
                f" EMA slope {_safe_round(weekly.get('ema13_slope'), 6)}, MACD Slope {_safe_round(weekly.get('macd_slope'), 6)}."
            ),
        ),
        _check(
            "Weekly Value-Zone Target Available",
            bool(weekly.get("weekly_value_target", {}).get("available")),
            (
                f"Weekly EMA13/EMA26 value zone "
                f"{_safe_round(weekly.get('weekly_value_target', {}).get('value_zone_low'))} ~ "
                f"{_safe_round(weekly.get('weekly_value_target', {}).get('value_zone_high'))}."
            ),
        ),
    ]

    daily_metrics = [
        _metric("Daily Decision", daily.get("state")),
        _metric("Daily Stage", _daily_state_label(daily.get("rsi_state"))),
        _metric("Setup Score", _safe_round(daily.get("setup_score"), 2)),
        _metric(
            "Triple-Screen Core Signals",
            f"{daily.get('elder_core_signal_count', 0)} / {daily.get('elder_core_signal_total', 3)}",
        ),
        _metric("2-Day Force EMA", _safe_round(daily.get("force_index_ema2"), 2), "accent"),
        _metric("Previous-Day Force EMA", _safe_round(daily.get("force_index_ema2_prev"), 2)),
        _metric("Force Change", _safe_round(daily.get("force_index_delta"), 2)),
        _metric("Daily Impulse Color", daily.get("impulse_color")),
        _metric("Supporting RSI", _safe_round(daily.get("rsi"), 2)),
        _metric("Supporting Histogram Change", _safe_round(daily.get("momentum_hist_delta"), 6)),
        _metric("13EMA", daily_ema13),
        _metric(
            "13EMA Value Band",
            (
                f"{_safe_round(daily.get('value_band_low'))} ~ {_safe_round(daily.get('value_band_high'))}"
                if daily.get("value_band_low") is not None and daily.get("value_band_high") is not None
                else "—"
            ),
        ),
        _metric("Distance to Value Band", _safe_round(daily.get("value_band_gap"))),
        _metric(daily.get("correction_counter_label", "Correction Closes in Last 8 Days"), daily.get("correction_count")),
        _metric("Structure Defense Level", _safe_round(daily.get("structure_break_level"))),
        _metric("Latest Close", latest_close),
        _metric("Custom Candle Confirmation", "Pass" if daily.get("custom_kline_confirmation") else "Fail"),
        _metric("Close vs Prior Close", "Pass" if daily.get("custom_close_rule_pass") else "Fail"),
        _metric("Candle Wick Ratio", f"{_safe_round(daily.get('custom_wick_ratio_pct'), 2)}%"),
        _metric("Wick Ratio >= 35%", "Pass" if daily.get("custom_wick_rule_pass") else "Fail"),
        _metric("Close Location in Candle", f"{_safe_round(daily.get('custom_close_location_pct'), 2)}%"),
        _metric("Close in Favorable Half", "Pass" if daily.get("custom_close_location_rule_pass") else "Fail"),
        _metric(
            "Daily Range",
            f"{latest_low if latest_low is not None else '—'} ~ {latest_high if latest_high is not None else '—'}",
        ),
    ]
    daily_checks = [
        _check(
            "2-Day Force Index Signal",
            daily.get("force_signal", False),
            (
                f"Current 2-Day Force EMA {_safe_round(daily.get('force_index_ema2'), 2)}."
                " For long, look for a break below 0; for short, look for a break above 0 that is not a multi-week new high."
            ),
        ),
        _check(
            "Daily Impulse System Not Opposing",
            daily.get("same_impulse_or_trend", False),
            (
                f"Daily impulse color {daily.get('impulse_color', '—')}; "
                "long cannot be red and short cannot be green."
            ),
        ),
        _check(
            "Structure Defense Level",
            daily.get("structure_intact", False),
            (
                f"Structure Defense Level {_safe_round(daily.get('structure_break_level'))}; "
                f" latest price range {latest_low if latest_low is not None else '—'} ~ {latest_high if latest_high is not None else '—'}."
            ),
        ),
        _check(
            "EMA Penetration Reference",
            bool(daily.get("entry_plan", {}).get("available")),
            (
                f"EMA penetration price {_safe_round(daily.get('entry_plan', {}).get('ema_penetration_entry'))}; "
                f"alternate breakout price {_safe_round(daily.get('entry_plan', {}).get('breakout_entry'))}."
            ),
        ),
        _check(
            "Custom Candle Confirmation",
            daily.get("custom_kline_confirmation", False),
            (
                "Supporting item, not part of the Elder core decision."
                f" Close vs Prior Close={'Pass' if daily.get('custom_close_rule_pass') else 'Fail'}; "
                f" Wick Ratio >= 35%={'Pass' if daily.get('custom_wick_rule_pass') else 'Fail'}; "
                f" close in favorable half={'Pass' if daily.get('custom_close_location_rule_pass') else 'Fail'}."
            ),
        ),
    ]

    weekly_divergence = divergence.get("weekly", {})
    daily_divergence = divergence.get("daily", {})
    strong_divergence = bool(weekly_divergence.get("strong_alert") or daily_divergence.get("strong_alert"))
    divergence_metrics = [
        _metric("Weekly Divergence", "Yes" if weekly_divergence.get("detected") else "No", "warn" if weekly_divergence.get("detected") else "neutral"),
        _metric("Daily Divergence", "Yes" if daily_divergence.get("detected") else "No", "warn" if daily_divergence.get("detected") else "neutral"),
        _metric("Strong Exhaustion Alert", "Yes" if strong_divergence else "No", "danger" if strong_divergence else "neutral"),
    ]
    key_levels = [
        _metric("Latest Daily Date", latest_bar_at),
        _metric("Latest Close", latest_close),
        _metric("SafeZone Initial Stop", _safe_round(safezone_stop)),
        _metric("Nick Stop", _safe_round(nick_stop)),
        _metric("ATR 1x Trailing Stop", _safe_round(atr_stop_1x)),
        _metric("ATR 2x Trailing Stop", _safe_round(atr_stop_2x)),
        _metric("Daily ATR", daily_atr),
        _metric("SafeZone Noise", _safe_round(safezone_noise)),
        _metric("Market Temperature", latest_temperature),
        _metric("Average Temperature", average_temperature),
    ]
    stop_method_cards = [
        {
            "code": method.get("code"),
            "label": method.get("label"),
            "raw_price": _safe_round(method.get("price"), 4) if method.get("price") is not None else None,
            "price": "Manual Required" if method.get("price") is None else str(_safe_round(method.get("price"), 4)),
            "reference": method.get("reference"),
            "suitable_for": method.get("suitable_for"),
            "detail": method.get("detail"),
            "source": method.get("source"),
            "style": method.get("style"),
            "group": method.get("group"),
            "auto": method.get("price") is not None,
        }
        for method in stop_methods
    ]
    initial_stop_methods = [method for method in stop_method_cards if method.get("group") == "initial"]
    trailing_stop_methods = [method for method in stop_method_cards if method.get("group") == "trailing"]

    summary = (
        f"System Decision: {followup['label']}."
        f" Weekly: {weekly.get('reason', 'No detail')}"
        f" Daily: {daily.get('reason', 'No detail')}"
    )

    return {
        "symbol": symbol,
        "generated_at": _utc_now_iso(),
        "source": "system",
        "model": model.to_dict(),
        "recommendation": followup,
        "summary": summary,
        "weekly": {
            "title": f"Weekly / {model.spec.label}",
            "subtitle": model.spec.weekly_model,
            "reason": weekly.get("reason"),
            "pass": weekly.get("pass", False),
            "actionable": weekly.get("actionable", False),
            "trend": weekly_trend,
            "metrics": weekly_metrics,
            "checks": weekly_checks,
            "raw": weekly,
        },
        "daily": {
            "title": "Daily / Setup",
            "subtitle": model.spec.daily_model,
            "reason": daily.get("reason"),
            "pass": daily.get("pass", False),
            "watch": daily.get("watch", False),
            "state": daily.get("state"),
            "state_label": _daily_state_label(daily.get("rsi_state")),
            "metrics": daily_metrics,
            "checks": daily_checks,
            "raw": daily,
        },
        "divergence": {
            "title": "Divergence / Risk Add-On",
            "summary": daily_divergence.get("reason") or weekly_divergence.get("reason") or "No divergence detail yet.",
            "metrics": divergence_metrics,
            "weekly": weekly_divergence,
            "daily": daily_divergence,
            "strong_alert": strong_divergence,
        },
        "key_levels": {
            "title": "Key Levels / Volatility Readings",
            "summary": "Helps identify watch focus and protective-stop placement.",
            "metrics": key_levels,
        },
        "execution": {
            "title": "Execution Plan / Trigger and Stops",
            "summary": execution_summary,
            "entry_price": suggested_entry,
            "stop_loss": suggested_stop,
            "target_price": suggested_target,
            "metrics": execution_metrics,
            "hourly": execution_hourly,
            "exits": execution_exits,
        },
        "stop_methods": {
            "title": "Elder Stop Methods",
            "summary": "Review how the initial stop defines risk, then how trailing stops advance after entry.",
            "initial_methods": initial_stop_methods,
            "trailing_methods": trailing_stop_methods,
            "methods": stop_method_cards,
        },
    }


def _prompt_outline() -> list[str]:
    return [
        "Weekly checks impulse color, MACD slope, EMA slope, and confirmed bars; the impulse system is only a blocking rule.",
        "Daily core uses 2-day Force Index EMA: long waits for a break below 0; short waits for a break above 0 that is not a multi-week new high; RSI and histogram are supporting context only.",
        "Provide current system execution levels: EMA penetration reference, alternate trigger one tick outside the previous-day high/low, current protective stop, and weekly value-zone target.",
        "Add weekly/daily divergence; current stop methods are SafeZone, Nick stop, and daily ATR 1x/2x trailing stops.",
        "Explicitly state where your AI view agrees or differs from the system recommendation.",
    ]


def _build_ai_messages(system_analysis: dict[str, Any]) -> list[dict[str, str]]:
    weekly_raw = system_analysis["weekly"]["raw"]
    daily_raw = system_analysis["daily"]["raw"]
    divergence = system_analysis["divergence"]
    recommendation = system_analysis["recommendation"]
    symbol = system_analysis["symbol"]

    user_prompt = {
        "task": "Based on the provided system technical data, give independent weekly/daily technical analysis and watch guidance for one ticker.",
        "requirements": _prompt_outline(),
        "symbol": symbol,
        "system_recommendation": recommendation,
        "weekly": {
            "trend": weekly_raw.get("trend"),
            "reason": system_analysis["weekly"].get("reason"),
            "metrics": system_analysis["weekly"].get("metrics"),
            "checks": system_analysis["weekly"].get("checks"),
        },
        "daily": {
            "state": daily_raw.get("state"),
            "reason": system_analysis["daily"].get("reason"),
            "metrics": system_analysis["daily"].get("metrics"),
            "checks": system_analysis["daily"].get("checks"),
        },
        "divergence": {
            "summary": divergence.get("summary"),
            "metrics": divergence.get("metrics"),
            "strong_alert": divergence.get("strong_alert"),
        },
        "key_levels": system_analysis.get("key_levels", {}).get("metrics", []),
        "execution": system_analysis.get("execution", {}),
        "stop_methods": system_analysis.get("stop_methods", {}).get("methods", []),
        "response_schema": {
            "stance": "bullish / bearish / neutral",
            "watch_decision": "priority watch / continue watching / do not watch",
            "confidence": "integer from 0 to 100",
            "weekly_analysis": {
                "summary": "1-2 sentence summary",
                "signals": ["list key weekly indicator conclusions"],
            },
            "daily_analysis": {
                "summary": "1-2 sentence summary",
                "signals": ["list key daily indicator conclusions"],
            },
            "investment_view": {
                "summary": "overall recommendation, emphasizing whether it is worth tracking",
                "risk_controls": ["risk points"],
                "key_level_focus": ["price levels or indicators to focus on"],
                "stop_method_comments": ["comment on the suitability of each stop method"],
                "execution_levels": ["comment on whether the system entry and stop prices are reasonable"],
            },
            "difference_vs_system": {
                "agreement": "one sentence explaining overall agreement or disagreement",
                "differences": ["list specific differences"],
            },
        },
        "strict_output": "Return only one JSON object. Do not use Markdown or add extra explanation.",
    }

    return [
        {
            "role": "system",
            "content": (
                "You are a technical-analysis assistant with a trading-execution perspective."
                "Separate weekly and daily output, and explicitly compare your view with the system-rule conclusion."
            ),
        },
        {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
    ]


def _extract_ai_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _request_ai_analysis(system_analysis: dict[str, Any]) -> dict[str, Any]:
    config = _ai_provider_config()
    if not config.enabled:
        return {
            "enabled": False,
            "status": "UNAVAILABLE",
            "model": config.model or "Not Configured",
            "outline": _prompt_outline(),
            "message": "AI model is not configured. Set TECH_ANALYSIS_AI_API_KEY / TECH_ANALYSIS_AI_MODEL, or use OPENAI_API_KEY / OPENAI_MODEL.",
        }

    response = requests.post(
        f"{config.base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "temperature": 0.3,
            "messages": _build_ai_messages(system_analysis),
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    content = (
        payload.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    parsed = _extract_ai_json(content)
    if parsed is None:
        return {
            "enabled": True,
            "status": "RAW",
            "model": config.model,
            "outline": _prompt_outline(),
            "raw_text": content,
            "message": "AI returned a result, but it could not be parsed as structured JSON; showing the raw response.",
        }

    return {
        "enabled": True,
        "status": "READY",
        "model": config.model,
        "outline": _prompt_outline(),
        "structured": parsed,
    }


def analyze_symbol(symbol: str, include_ai: bool = True, model_id: str | None = None) -> dict[str, Any]:
    normalized_symbol = _normalize_symbol(symbol)
    if not normalized_symbol:
        raise TechnicalAnalysisError("Enter a valid ticker.")

    try:
        system_analysis = _build_system_analysis(normalized_symbol, model_id=model_id)
    except Exception as exc:
        raise TechnicalAnalysisError(f"System analysis failed: {exc}") from exc

    ai_analysis: dict[str, Any]
    if include_ai:
        try:
            ai_analysis = _request_ai_analysis(system_analysis)
        except Exception as exc:
            ai_analysis = {
                "enabled": True,
                "status": "ERROR",
                "outline": _prompt_outline(),
                "message": f"AI analysis call failed: {exc}",
            }
    else:
        ai_analysis = {
            "enabled": False,
            "status": "SKIPPED",
            "outline": _prompt_outline(),
            "message": "AI analysis was disabled for this request.",
        }

    return {
        "symbol": normalized_symbol,
        "generated_at": _utc_now_iso(),
        "system": _json_safe(system_analysis),
        "ai": _json_safe(ai_analysis),
    }
