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
        "NEUTRAL": "中性",
        "NO_PULLBACK": "周线做多，但日线还没形成清晰回调",
        "NO_RALLY": "周线做空，但日线还没形成清晰反弹",
        "STRUCTURE_BROKEN": "结构被破坏",
        "ACCELERATING_PULLBACK": "回调仍在加速",
        "ACCELERATING_RALLY": "反弹仍在加速",
        "PULLBACK_WAIT_VALUE_BAND": "回调已出现，等待回到 13EMA 价值带",
        "RALLY_WAIT_VALUE_BAND": "反弹已出现，等待回到 13EMA 价值带",
        "PULLBACK_WAIT_HISTOGRAM": "已回到 13EMA 价值带，等待 Histogram 回升",
        "RALLY_WAIT_HISTOGRAM": "已回到 13EMA 价值带，等待 Histogram 回落",
        "PULLBACK_HISTOGRAM_TURNED": "回调到价值带后，Histogram 已回升",
        "RALLY_HISTOGRAM_TURNED": "反弹到价值带后，Histogram 已回落",
        "PULLBACK_WAIT_FORCE_BELOW_ZERO": "等待 2日 Force EMA 跌破 0",
        "PULLBACK_FORCE_READY_WAIT_IMPULSE": "Force 已到位，等待日线动力系统不再反向",
        "PULLBACK_FORCE_BELOW_ZERO": "2日 Force EMA 已跌破 0",
        "RALLY_WAIT_FORCE_ABOVE_ZERO": "等待 2日 Force EMA 升破 0",
        "RALLY_FORCE_READY_WAIT_IMPULSE": "Force 已到位，等待日线动力系统不再反向",
        "RALLY_FORCE_ABOVE_ZERO": "2日 Force EMA 已升破 0",
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
            "timeframe": "周线/日线",
            "direction": direction,
            "reason": "周线方向未明确，当前不单独评估趋势背离。",
        }
        return {"weekly": neutral_note, "daily": neutral_note}

    return {
        "weekly": indicators.detect_divergence(
            weekly_frame,
            settings.strategy,
            direction,
            "周线",
            settings.qualification.strong_divergence_exhaustion_multiplier,
        ),
        "daily": indicators.detect_divergence(
            daily_frame,
            settings.strategy,
            direction,
            "日线",
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
            "label": "暂不跟进观察",
            "tone": "warn",
            "reason": "周线柱线方向还不清晰，当前没有稳定的趋势主线。",
        }

    if weekly.get("pass") and daily.get("pass") and not strong_divergence:
        return {
            "code": "READY",
            "label": "可以重点跟进观察",
            "tone": "safe",
            "reason": "周线趋势确认、日线 setup 到位，当前已经具备重点盯盘价值。",
        }

    if weekly.get("pass") and daily.get("pass") and strong_divergence:
        return {
            "code": "READY_WITH_CAUTION",
            "label": "可以观察，但要更保守",
            "tone": "warn",
            "reason": "周线和日线条件都成立，但背离提示趋势可能衰竭，执行上要降低激进程度。",
        }

    if weekly.get("pass") and daily.get("watch"):
        return {
            "code": "WATCH",
            "label": "可以继续跟进观察",
            "tone": "info",
            "reason": "周线方向已经基本明确，但日线 Force Index 或日线动力系统还没有完全到位。",
        }

    if weekly.get("actionable") and not weekly.get("pass"):
        return {
            "code": "EARLY_TREND",
            "label": "先放入跟踪，不急着判断",
            "tone": "info",
            "reason": "周线已经出现方向，但确认条数或趋势侧位置还不够完整。",
        }

    return {
        "code": "REJECT",
        "label": "暂不跟进观察",
        "tone": "warn",
        "reason": daily.get("reject_reason") or "日线 setup 质量不足，暂不建议投入更多关注。",
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
    execution_summary = "周线方向未明确时，不提供执行价位。"
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
                    f"当前执行口径：优先关注 EMA 穿透参考价 {suggested_entry if suggested_entry is not None else '—'}；"
                    f"替代触发价为前一日高/低点外一跳 {entry_plan.get('breakout_entry', '—')}。"
                    f" 初始止损仍需在 SafeZone / 尼克止损法之间手动选择。"
                )
                execution_metrics = [
                    _metric("EMA 穿透参考价", suggested_entry, "accent"),
                    _metric("前日突破参考价", _safe_round(entry_plan.get("breakout_entry")), "accent"),
                    _metric("明日EMA估算", _safe_round(entry_plan.get("projected_next_ema"))),
                    _metric("平均穿透", _safe_round(entry_plan.get("average_penetration"))),
                    _metric("SafeZone 初始止损", _safe_round(execution_exits.get("initial_stop_safezone")), "warn"),
                    _metric("尼克止损", _safe_round(execution_exits.get("initial_stop_nick")), "warn"),
                    _metric("尼克参考日期", execution_exits.get("initial_stop_nick_reference_date")),
                    _metric("ATR 1x 移动止损", suggested_stop, "warn"),
                    _metric("ATR 2x 移动止损", _safe_round(execution_exits.get("stop_loss_atr_2x"))),
                    _metric("周线价值区间目标", suggested_target),
                    _metric(
                        "周线价值区间",
                        (
                            f"{_safe_round(weekly_value_target.get('value_zone_low'))} ~ "
                            f"{_safe_round(weekly_value_target.get('value_zone_high'))}"
                        )
                        if weekly_value_target.get("available")
                        else "—",
                    ),
                    _metric("内部模型盈亏比", _safe_round(execution_exits.get("reward_risk_ratio_model"), 2)),
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
                f"当前执行口径：建议关注触发价 {suggested_entry if suggested_entry is not None else '—'}，"
                f"初始止损需在 SafeZone / 尼克止损法之间手动选择；"
                f"ATR 1x 移动止损参考位 {suggested_stop if suggested_stop is not None else '—'}。"
                f" {execution_hourly.get('reason', '')}"
            )
            execution_metrics = [
                _metric("建议入场价", suggested_entry, "accent"),
                _metric("SafeZone 初始止损", _safe_round(execution_exits.get("initial_stop_safezone")), "warn"),
                _metric("尼克止损", _safe_round(execution_exits.get("initial_stop_nick")), "warn"),
                _metric("ATR 1x 移动止损", suggested_stop, "warn"),
                _metric("ATR 2x 移动止损", _safe_round(execution_exits.get("stop_loss_atr_2x"))),
                _metric("首个目标位", suggested_target),
                _metric("小时线状态", execution_hourly.get("status")),
                _metric("当前价", _safe_round(execution_hourly.get("close"))),
                _metric("信号K高点", _safe_round(execution_hourly.get("signal_bar_high"))),
                _metric("信号K低点", _safe_round(execution_hourly.get("signal_bar_low"))),
                _metric("ATR", _safe_round(execution_hourly.get("atr"), 4)),
                _metric("内部模型盈亏比", _safe_round(execution_exits.get("reward_risk_ratio_model"), 2)),
            ]
        else:
            execution_summary = execution_hourly.get("reason") or "当前无法生成执行价位。"
            execution_metrics = [
                _metric("建议买入价", None),
                _metric("当前保护止损", None),
                _metric("小时线状态", execution_hourly.get("status")),
            ]

    weekly_metrics = [
        _metric("周线趋势", weekly.get("trend")),
        _metric("动力系统颜色", weekly.get("impulse_color"), "accent"),
        _metric("MACD", _safe_round(weekly.get("macd"), 6)),
        _metric("MACD 斜率", _safe_round(weekly.get("macd_slope"), 6)),
        _metric("Signal", _safe_round(weekly.get("macd_signal"), 6)),
        _metric("Histogram", _safe_round(weekly.get("histogram"), 6)),
        _metric("Histogram 变化", _safe_round(weekly.get("histogram_delta"), 6)),
        _metric("13EMA", _safe_round(weekly.get("ema13"), 4)),
        _metric("13EMA 斜率", _safe_round(weekly.get("ema13_slope"), 6)),
        _metric("确认 bars", f"{weekly.get('confirmed_bars', 0)} / {settings.strategy.weekly.confirm_bars}"),
        _metric("趋势分数", _safe_round(weekly.get("trend_score"), 2)),
    ]
    weekly_checks = [
        _check(
            "MACD 斜率方向",
            weekly.get("actionable", False),
            f"本周 MACD 斜率 {_safe_round(weekly.get('macd_slope'), 6)}，不为 0 才有方向。",
        ),
        _check(
            "连续同向 MACD bars",
            bool(weekly.get("pass_checks", {}).get("confirmed_bars")),
            f"当前 {weekly.get('confirmed_bars', 0)} 根，规则要求至少 {settings.strategy.weekly.confirm_bars} 根。",
        ),
        _check(
            "动力系统禁止规则",
            bool(weekly.get("pass_checks", {}).get("impulse_aligned")),
            (
                f"动力颜色 {weekly.get('impulse_color', '—')}；做多不能为红色，做空不能为绿色。"
                f" EMA 斜率 {_safe_round(weekly.get('ema13_slope'), 6)}，MACD 斜率 {_safe_round(weekly.get('macd_slope'), 6)}。"
            ),
        ),
        _check(
            "周线价值区间目标可用",
            bool(weekly.get("weekly_value_target", {}).get("available")),
            (
                f"周线 EMA13/EMA26 价值区间 "
                f"{_safe_round(weekly.get('weekly_value_target', {}).get('value_zone_low'))} ~ "
                f"{_safe_round(weekly.get('weekly_value_target', {}).get('value_zone_high'))}。"
            ),
        ),
    ]

    daily_metrics = [
        _metric("日线结论", daily.get("state")),
        _metric("日线阶段", _daily_state_label(daily.get("rsi_state"))),
        _metric("Setup 分数", _safe_round(daily.get("setup_score"), 2)),
        _metric(
            "三重滤网核心信号",
            f"{daily.get('elder_core_signal_count', 0)} / {daily.get('elder_core_signal_total', 3)}",
        ),
        _metric("2日 Force EMA", _safe_round(daily.get("force_index_ema2"), 2), "accent"),
        _metric("前一日 Force EMA", _safe_round(daily.get("force_index_ema2_prev"), 2)),
        _metric("Force 变化", _safe_round(daily.get("force_index_delta"), 2)),
        _metric("日线动力颜色", daily.get("impulse_color")),
        _metric("辅助 RSI", _safe_round(daily.get("rsi"), 2)),
        _metric("辅助 Histogram 变化", _safe_round(daily.get("momentum_hist_delta"), 6)),
        _metric("13EMA", daily_ema13),
        _metric(
            "13EMA 价值带",
            (
                f"{_safe_round(daily.get('value_band_low'))} ~ {_safe_round(daily.get('value_band_high'))}"
                if daily.get("value_band_low") is not None and daily.get("value_band_high") is not None
                else "—"
            ),
        ),
        _metric("距价值带", _safe_round(daily.get("value_band_gap"))),
        _metric(daily.get("correction_counter_label", "近8日修正收盘数"), daily.get("correction_count")),
        _metric("结构防守位", _safe_round(daily.get("structure_break_level"))),
        _metric("最新收盘", latest_close),
        _metric("自定义K线确认", "成立" if daily.get("custom_kline_confirmation") else "未成立"),
        _metric("收盘相对昨收", "满足" if daily.get("custom_close_rule_pass") else "未满足"),
        _metric("K线影线占比", f"{_safe_round(daily.get('custom_wick_ratio_pct'), 2)}%"),
        _metric("影线比例>=35%", "满足" if daily.get("custom_wick_rule_pass") else "未满足"),
        _metric("收盘在K线中的位置", f"{_safe_round(daily.get('custom_close_location_pct'), 2)}%"),
        _metric("收盘落在有利半区", "满足" if daily.get("custom_close_location_rule_pass") else "未满足"),
        _metric(
            "当日区间",
            f"{latest_low if latest_low is not None else '—'} ~ {latest_high if latest_high is not None else '—'}",
        ),
    ]
    daily_checks = [
        _check(
            "2日 Force Index 信号",
            daily.get("force_signal", False),
            (
                f"当前 2日 Force EMA {_safe_round(daily.get('force_index_ema2'), 2)}。"
                " 做多看跌破 0，做空看升破 0 且不是几周新高。"
            ),
        ),
        _check(
            "日线动力系统不反向",
            daily.get("same_impulse_or_trend", False),
            (
                f"日线动力颜色 {daily.get('impulse_color', '—')}；"
                "做多不能为红色，做空不能为绿色。"
            ),
        ),
        _check(
            "结构防守位",
            daily.get("structure_intact", False),
            (
                f"结构防守位 {_safe_round(daily.get('structure_break_level'))}；"
                f" 最新价格区间 {latest_low if latest_low is not None else '—'} ~ {latest_high if latest_high is not None else '—'}。"
            ),
        ),
        _check(
            "EMA 穿透参考价",
            bool(daily.get("entry_plan", {}).get("available")),
            (
                f"EMA 穿透价 {_safe_round(daily.get('entry_plan', {}).get('ema_penetration_entry'))}；"
                f"替代突破价 {_safe_round(daily.get('entry_plan', {}).get('breakout_entry'))}。"
            ),
        ),
        _check(
            "自定义K线确认",
            daily.get("custom_kline_confirmation", False),
            (
                "辅助项，不参与 Elder 核心判断。"
                f" 收盘相对昨收={'满足' if daily.get('custom_close_rule_pass') else '未满足'}；"
                f" 影线比例>=35%={'满足' if daily.get('custom_wick_rule_pass') else '未满足'}；"
                f" 收盘位于有利半区={'满足' if daily.get('custom_close_location_rule_pass') else '未满足'}。"
            ),
        ),
    ]

    weekly_divergence = divergence.get("weekly", {})
    daily_divergence = divergence.get("daily", {})
    strong_divergence = bool(weekly_divergence.get("strong_alert") or daily_divergence.get("strong_alert"))
    divergence_metrics = [
        _metric("周线背离", "有" if weekly_divergence.get("detected") else "无", "warn" if weekly_divergence.get("detected") else "neutral"),
        _metric("日线背离", "有" if daily_divergence.get("detected") else "无", "warn" if daily_divergence.get("detected") else "neutral"),
        _metric("强衰竭提醒", "有" if strong_divergence else "无", "danger" if strong_divergence else "neutral"),
    ]
    key_levels = [
        _metric("最新日线日期", latest_bar_at),
        _metric("最新收盘", latest_close),
        _metric("SafeZone 初始止损", _safe_round(safezone_stop)),
        _metric("尼克止损", _safe_round(nick_stop)),
        _metric("ATR 移动止损 1x", _safe_round(atr_stop_1x)),
        _metric("ATR 移动止损 2x", _safe_round(atr_stop_2x)),
        _metric("日线 ATR", daily_atr),
        _metric("SafeZone 噪音", _safe_round(safezone_noise)),
        _metric("市场温度", latest_temperature),
        _metric("平均温度", average_temperature),
    ]
    stop_method_cards = [
        {
            "code": method.get("code"),
            "label": method.get("label"),
            "raw_price": _safe_round(method.get("price"), 4) if method.get("price") is not None else None,
            "price": "需手工判断" if method.get("price") is None else str(_safe_round(method.get("price"), 4)),
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
        f"系统结论：{followup['label']}。"
        f" 周线：{weekly.get('reason', '暂无说明')}"
        f" 日线：{daily.get('reason', '暂无说明')}"
    )

    return {
        "symbol": symbol,
        "generated_at": _utc_now_iso(),
        "source": "system",
        "model": model.to_dict(),
        "recommendation": followup,
        "summary": summary,
        "weekly": {
            "title": f"周线 / {model.spec.label}",
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
            "title": "日线 / Setup",
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
            "title": "背离 / 风险补充",
            "summary": daily_divergence.get("reason") or weekly_divergence.get("reason") or "暂无背离说明。",
            "metrics": divergence_metrics,
            "weekly": weekly_divergence,
            "daily": daily_divergence,
            "strong_alert": strong_divergence,
        },
        "key_levels": {
            "title": "关键价位 / 波动读数",
            "summary": "帮助你判断观察重点和保护性止损位置。",
            "metrics": key_levels,
        },
        "execution": {
            "title": "执行计划 / 触发价与止损",
            "summary": execution_summary,
            "entry_price": suggested_entry,
            "stop_loss": suggested_stop,
            "target_price": suggested_target,
            "metrics": execution_metrics,
            "hourly": execution_hourly,
            "exits": execution_exits,
        },
        "stop_methods": {
            "title": "Elder 止损方法",
            "summary": "先看初始止损怎么定义风险，再看持仓后的跟踪止损怎么推进。",
            "initial_methods": initial_stop_methods,
            "trailing_methods": trailing_stop_methods,
            "methods": stop_method_cards,
        },
    }


def _prompt_outline() -> list[str]:
    return [
        "周线看动力系统颜色、MACD 斜率、EMA 斜率、确认 bars；动力系统只做禁止规则。",
        "日线核心看 2日 Force Index EMA：做多等待跌破 0，做空等待升破 0 且不是几周新高；RSI 与 Histogram 仅作辅助说明。",
        "给出系统当前执行价位：EMA 穿透参考价、前一日高/低点外一跳的替代触发价、当前保护止损、周线价值区间目标。",
        "补充看周线/日线背离；当前止损口径先收敛为 SafeZone、尼克止损法，以及日线 ATR 1x/2x 移动止损。",
        "明确写出系统建议与你的 AI 建议一致或不一致的地方。",
    ]


def _build_ai_messages(system_analysis: dict[str, Any]) -> list[dict[str, str]]:
    weekly_raw = system_analysis["weekly"]["raw"]
    daily_raw = system_analysis["daily"]["raw"]
    divergence = system_analysis["divergence"]
    recommendation = system_analysis["recommendation"]
    symbol = system_analysis["symbol"]

    user_prompt = {
        "task": "基于给定的系统技术面数据，对单只股票给出你的独立周线/日线技术分析和观察建议。",
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
            "stance": "看多 / 看空 / 中性",
            "watch_decision": "重点观察 / 继续观察 / 暂不观察",
            "confidence": "0-100 的整数",
            "weekly_analysis": {
                "summary": "1-2 句总结",
                "signals": ["列出周线关键指标结论"],
            },
            "daily_analysis": {
                "summary": "1-2 句总结",
                "signals": ["列出日线关键指标结论"],
            },
            "investment_view": {
                "summary": "整体建议，强调是否适合继续跟踪",
                "risk_controls": ["风险点"],
                "key_level_focus": ["应重点观察的价位或指标"],
                "stop_method_comments": ["分别点评不同止损方法的适用性"],
                "execution_levels": ["点评系统给出的买入价和止损价是否合理"],
            },
            "difference_vs_system": {
                "agreement": "一句话说明整体一致还是分歧",
                "differences": ["列出具体差异点"],
            },
        },
        "strict_output": "只返回一个 JSON 对象，不要使用 Markdown，不要添加额外说明。",
    }

    return [
        {
            "role": "system",
            "content": (
                "你是一名偏交易执行视角的技术分析助手。"
                "你需要按周线和日线分开输出，并且显式比较你与系统规则结论的差异。"
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
            "model": config.model or "未配置",
            "outline": _prompt_outline(),
            "message": "AI 模型尚未配置。请设置 TECH_ANALYSIS_AI_API_KEY / TECH_ANALYSIS_AI_MODEL，或使用 OPENAI_API_KEY / OPENAI_MODEL。",
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
            "message": "AI 已返回结果，但未能解析成结构化 JSON，当前按原文展示。",
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
        raise TechnicalAnalysisError("请输入有效的股票代码。")

    try:
        system_analysis = _build_system_analysis(normalized_symbol, model_id=model_id)
    except Exception as exc:
        raise TechnicalAnalysisError(f"系统分析失败：{exc}") from exc

    ai_analysis: dict[str, Any]
    if include_ai:
        try:
            ai_analysis = _request_ai_analysis(system_analysis)
        except Exception as exc:
            ai_analysis = {
                "enabled": True,
                "status": "ERROR",
                "outline": _prompt_outline(),
                "message": f"AI 分析调用失败：{exc}",
            }
    else:
        ai_analysis = {
            "enabled": False,
            "status": "SKIPPED",
            "outline": _prompt_outline(),
            "message": "本次请求未启用 AI 分析。",
        }

    return {
        "symbol": normalized_symbol,
        "generated_at": _utc_now_iso(),
        "system": _json_safe(system_analysis),
        "ai": _json_safe(ai_analysis),
    }
