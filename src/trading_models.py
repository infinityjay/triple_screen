from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Callable

import pandas as pd

import indicators
from config.schema import StrategyConfig, TradePlanConfig


ELDER_FORCE_MODEL_ID = "elder_force"
VALUE_REVERSAL_MODEL_ID = "value_reversal"
DEFAULT_MODEL_ID = VALUE_REVERSAL_MODEL_ID

MODEL_ID_ALIASES = {
    "current": ELDER_FORCE_MODEL_ID,
    "legacy_pre_45c9b2d": VALUE_REVERSAL_MODEL_ID,
}


@dataclass(frozen=True)
class TradingModelSpec:
    id: str
    label: str
    description: str
    weekly_model: str
    daily_model: str
    intraday_trigger: str
    exit_model: str


@dataclass(frozen=True)
class IntradayPlan:
    hourly: dict[str, Any]
    exits: dict[str, Any]
    trigger_source: str


ScreenWeeklyFn = Callable[[pd.DataFrame | None, StrategyConfig], dict]
ScreenDailyFn = Callable[[pd.DataFrame | None, str, StrategyConfig], dict]


@dataclass(frozen=True)
class TradingModel:
    spec: TradingModelSpec
    screen_weekly_fn: ScreenWeeklyFn
    screen_daily_fn: ScreenDailyFn
    use_weekly_value_target: bool
    use_planned_daily_entry: bool

    @property
    def id(self) -> str:
        return self.spec.id

    def screen_weekly(self, frame: pd.DataFrame | None, settings: StrategyConfig) -> dict:
        return self.screen_weekly_fn(frame, settings)

    def screen_daily(self, frame: pd.DataFrame | None, trend: str, settings: StrategyConfig) -> dict:
        return self.screen_daily_fn(frame, trend, settings)

    def calc_exits(
        self,
        direction: str,
        entry: float,
        daily_frame: pd.DataFrame | None,
        atr: float,
        trade_plan: TradePlanConfig,
        signal_bar_high: float | None = None,
        signal_bar_low: float | None = None,
        weekly_frame: pd.DataFrame | None = None,
        hourly_frame: pd.DataFrame | None = None,
    ) -> dict:
        if self.use_weekly_value_target:
            return indicators.calc_exits(
                direction,
                entry,
                daily_frame,
                atr,
                trade_plan,
                signal_bar_high=signal_bar_high,
                signal_bar_low=signal_bar_low,
                weekly_frame=weekly_frame,
                hourly_frame=hourly_frame,
            )
        return legacy_calc_exits(
            direction,
            entry,
            daily_frame,
            atr,
            trade_plan,
            signal_bar_high=signal_bar_high,
            signal_bar_low=signal_bar_low,
        )

    def build_intraday_plan(
        self,
        direction: str,
        daily_frame: pd.DataFrame | None,
        weekly_frame: pd.DataFrame | None,
        hourly_frame: pd.DataFrame | None,
        settings: StrategyConfig,
        trade_plan: TradePlanConfig,
        as_of: datetime | None = None,
        current_bar: pd.Series | None = None,
    ) -> IntradayPlan | None:
        if self.use_planned_daily_entry:
            return _build_current_intraday_plan(
                direction=direction,
                daily_frame=daily_frame,
                weekly_frame=weekly_frame,
                hourly_frame=hourly_frame,
                settings=settings,
                trade_plan=trade_plan,
                current_bar=current_bar,
            )
        return _build_legacy_intraday_plan(
            direction=direction,
            daily_frame=daily_frame,
            weekly_frame=weekly_frame,
            hourly_frame=hourly_frame,
            settings=settings,
            trade_plan=trade_plan,
            as_of=as_of,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.spec)


def _first_available_bar(hourly_frame: pd.DataFrame | None, current_bar: pd.Series | None) -> pd.Series | None:
    if current_bar is not None:
        return current_bar
    if hourly_frame is None or hourly_frame.empty:
        return None
    return hourly_frame.iloc[-1]


def _hourly_atr(hourly_frame: pd.DataFrame | None, settings: StrategyConfig) -> float:
    if hourly_frame is None or len(hourly_frame) < settings.hourly.atr_period + 1:
        return 0.0
    return float(indicators.calc_atr(hourly_frame, settings.hourly.atr_period).iloc[-1])


def get_planned_trigger(
    direction: str,
    entry_plan: dict[str, Any],
    current_bar: pd.Series,
) -> tuple[float | None, str | None, bool, bool, bool, bool]:
    primary_entry = entry_plan.get("ema_penetration_entry")
    breakout_entry = entry_plan.get("breakout_entry")
    current_open = float(current_bar["open"])
    current_high = float(current_bar["high"])
    current_low = float(current_bar["low"])
    current_close = float(current_bar["close"])
    candle_range = max(current_high - current_low, 1e-9)
    upper_half_close = current_close >= current_low + candle_range * 0.5
    lower_half_close = current_close <= current_low + candle_range * 0.5
    upper_40_close = current_close >= current_low + candle_range * 0.6
    lower_40_close = current_close <= current_low + candle_range * 0.4
    primary_touched = False
    breakout_touched = False
    primary_confirmed = False
    breakout_confirmed = False

    if direction == "LONG":
        primary_touched = primary_entry is not None and current_low <= float(primary_entry)
        breakout_touched = breakout_entry is not None and current_high >= float(breakout_entry)
        primary_confirmed = (
            primary_touched
            and current_close >= float(primary_entry)
            and current_close > current_open
            and upper_40_close
        )
        breakout_confirmed = breakout_touched and current_close >= float(breakout_entry) and upper_half_close
    elif direction == "SHORT":
        primary_touched = primary_entry is not None and current_high >= float(primary_entry)
        breakout_touched = breakout_entry is not None and current_low <= float(breakout_entry)
        primary_confirmed = (
            primary_touched
            and current_close <= float(primary_entry)
            and current_close < current_open
            and lower_40_close
        )
        breakout_confirmed = breakout_touched and current_close <= float(breakout_entry) and lower_half_close

    if primary_confirmed and primary_entry is not None:
        return round(float(primary_entry), 4), "EMA_PENETRATION", primary_touched, breakout_touched, primary_confirmed, breakout_confirmed
    if breakout_confirmed and breakout_entry is not None:
        return round(float(breakout_entry), 4), "PREVIOUS_DAY_BREAK", primary_touched, breakout_touched, primary_confirmed, breakout_confirmed
    return None, None, primary_touched, breakout_touched, primary_confirmed, breakout_confirmed


def _build_entry_options(
    direction: str,
    entry_plan: dict[str, Any],
    daily_frame: pd.DataFrame,
    weekly_frame: pd.DataFrame | None,
    atr: float,
    trade_plan: TradePlanConfig,
    primary_touched: bool,
    breakout_touched: bool,
    primary_confirmed: bool,
    breakout_confirmed: bool,
) -> list[dict[str, Any]]:
    option_specs = [
        (
            "EMA_PENETRATION",
            "EMA penetration reference",
            entry_plan.get("ema_penetration_entry"),
            primary_touched,
            primary_confirmed,
        ),
        (
            "PREVIOUS_DAY_BREAK",
            "Previous-day break reference",
            entry_plan.get("breakout_entry"),
            breakout_touched,
            breakout_confirmed,
        ),
    ]
    options: list[dict[str, Any]] = []
    for code, label, price, touched, confirmed in option_specs:
        if price is None:
            continue
        entry = round(float(price), 4)
        options.append(
            {
                "code": code,
                "label": label,
                "price": entry,
                "touched": bool(touched),
                "triggered": bool(confirmed),
                "exits": indicators.calc_exits(
                    direction,
                    entry,
                    daily_frame,
                    atr,
                    trade_plan,
                    weekly_frame=weekly_frame,
                ),
            }
        )
    return options


def _build_current_intraday_plan(
    direction: str,
    daily_frame: pd.DataFrame | None,
    weekly_frame: pd.DataFrame | None,
    hourly_frame: pd.DataFrame | None,
    settings: StrategyConfig,
    trade_plan: TradePlanConfig,
    current_bar: pd.Series | None,
) -> IntradayPlan | None:
    bar = _first_available_bar(hourly_frame, current_bar)
    if daily_frame is None or daily_frame.empty or bar is None:
        return None

    entry_plan = indicators.calc_ema_penetration_entry_plan(daily_frame, direction)
    (
        entry_price,
        trigger_source,
        primary_touched,
        breakout_touched,
        primary_confirmed,
        breakout_confirmed,
    ) = get_planned_trigger(direction, entry_plan, bar)
    atr = _hourly_atr(hourly_frame, settings)
    entry_options = _build_entry_options(
        direction,
        entry_plan,
        daily_frame,
        weekly_frame,
        atr,
        trade_plan,
        primary_touched,
        breakout_touched,
        primary_confirmed,
        breakout_confirmed,
    )
    if not entry_options:
        return None

    selected_option = next((option for option in entry_options if option["triggered"]), entry_options[0])
    entry_price = selected_option["price"]
    trigger_source = selected_option["code"]
    triggered = bool(primary_confirmed or breakout_confirmed)
    triggered_labels = [option["label"] for option in entry_options if option["triggered"]]
    touched_labels = [option["label"] for option in entry_options if option["touched"]]
    trigger_score = 4.0 if primary_confirmed else 3.5 if breakout_confirmed else 0.0
    touched = bool(primary_touched or breakout_touched)
    status = "TRIGGERED" if triggered else "TOUCHED_ENTRY_PRICE" if touched else "WAITING_ENTRY_PRICE"
    hourly = {
        "close": round(float(bar["close"]), 4),
        "current_high": round(float(bar["high"]), 4),
        "current_low": round(float(bar["low"]), 4),
        "high_n": round(float(bar["high"]), 4),
        "low_n": round(float(bar["low"]), 4),
        "signal_bar_high": None,
        "signal_bar_low": None,
        "atr": round(atr, 4),
        "status": status,
        "entry_price": round(float(entry_price), 4),
        "ema_penetration_entry": entry_plan.get("ema_penetration_entry"),
        "previous_day_break_entry": entry_plan.get("breakout_entry"),
        "primary_entry_touched": primary_touched,
        "breakout_entry_touched": breakout_touched,
        "primary_entry_confirmed": primary_confirmed,
        "breakout_entry_confirmed": breakout_confirmed,
        "breakout_long": triggered if direction == "LONG" else False,
        "breakout_short": triggered if direction == "SHORT" else False,
        "trigger_source": trigger_source,
        "trigger_sources": [option["code"] for option in entry_options if option["triggered"]],
        "trigger_score": trigger_score,
        "pass": triggered,
        "entry_plan": entry_plan,
        "entry_options": entry_options,
        "reason": (
            f"Price touched {entry_plan.get('trigger_label', 'trade')}: {', '.join(triggered_labels)}"
            if triggered
            else (
                f"Price touched the reference level, but the hourly bar has not confirmed reclaim/hold: {', '.join(touched_labels)}"
                if touched_labels
                else entry_plan.get("reason", "Waiting for price trigger")
            )
        ),
    }
    return IntradayPlan(hourly=hourly, exits=selected_option["exits"], trigger_source=trigger_source)


def _build_legacy_intraday_plan(
    direction: str,
    daily_frame: pd.DataFrame | None,
    weekly_frame: pd.DataFrame | None,
    hourly_frame: pd.DataFrame | None,
    settings: StrategyConfig,
    trade_plan: TradePlanConfig,
    as_of: datetime | None,
) -> IntradayPlan | None:
    if daily_frame is None or daily_frame.empty or hourly_frame is None or hourly_frame.empty:
        return None
    hourly = indicators.screen_hourly(hourly_frame, direction, settings, as_of=as_of)
    if "entry_price" not in hourly:
        return None
    exits = legacy_calc_exits(
        direction,
        float(hourly["entry_price"]),
        daily_frame,
        float(hourly.get("atr", 0.0) or 0.0),
        trade_plan,
        signal_bar_high=hourly.get("signal_bar_high"),
        signal_bar_low=hourly.get("signal_bar_low"),
    )
    return IntradayPlan(hourly=hourly, exits=exits, trigger_source="HOURLY_TRAILING_BAR")


def legacy_screen_weekly(df_week: pd.DataFrame | None, settings: StrategyConfig) -> dict:
    required = settings.weekly.macd_slow + settings.weekly.macd_signal + 5
    if df_week is None or len(df_week) < required:
        return {"trend": "NEUTRAL", "pass": False, "actionable": False, "reason": "Insufficient weekly data"}

    macd, signal, histogram = indicators.calc_macd(df_week, settings)
    close = df_week["close"].astype(float)
    ema13 = indicators.calc_ema(close, indicators.DAILY_EMA_PERIOD)
    hist_now = float(histogram.iloc[-1])
    hist_prev = float(histogram.iloc[-2])
    hist_delta = hist_now - hist_prev
    close_now = float(close.iloc[-1])
    ema_now = float(ema13.iloc[-1])
    ema_prev = float(ema13.iloc[-2])
    ema_delta = ema_now - ema_prev
    histogram_deltas = histogram.diff().dropna()

    confirmed = 0
    for value in reversed(histogram_deltas.values):
        if (hist_delta > 0 and value > 0) or (hist_delta < 0 and value < 0):
            confirmed += 1
        else:
            break

    if hist_delta > 0:
        trend = "LONG"
    elif hist_delta < 0:
        trend = "SHORT"
    else:
        trend = "NEUTRAL"

    impulse = "RISING" if hist_delta > 0 else "FALLING" if hist_delta < 0 else "FLAT"
    actionable = trend != "NEUTRAL"
    confirmed_pass = confirmed >= settings.weekly.confirm_bars
    impulse_aligned = (
        (trend == "LONG" and ema_delta > 0 and hist_delta > 0)
        or (trend == "SHORT" and ema_delta < 0 and hist_delta < 0)
        or trend == "NEUTRAL"
    )
    close_on_trend_side = (
        (trend == "LONG" and close_now > ema_now)
        or (trend == "SHORT" and close_now < ema_now)
        or trend == "NEUTRAL"
    )
    trend_score = 0.0
    if actionable:
        trend_score += min(abs(float(hist_delta)) * 40, 2.5)
        trend_score += min(confirmed, 4) * 0.35
        if impulse_aligned:
            trend_score += 0.5
        if (trend == "LONG" and hist_now < 0) or (trend == "SHORT" and hist_now > 0):
            trend_score += 0.8

    if trend == "LONG":
        setup_state = "BULLISH_SLOPE"
        reason = (
            f"Histogram {hist_prev:+.4f} -> {hist_now:+.4f} (rising); "
            f"13EMA slope {ema_delta:+.4f} ({'rising' if ema_delta > 0 else 'not rising'}); "
            f"confirmed bars {confirmed}/{settings.weekly.confirm_bars}."
        )
    elif trend == "SHORT":
        setup_state = "BEARISH_SLOPE"
        reason = (
            f"Histogram {hist_prev:+.4f} -> {hist_now:+.4f} (falling); "
            f"13EMA slope {ema_delta:+.4f} ({'falling' if ema_delta < 0 else 'not falling'}); "
            f"confirmed bars {confirmed}/{settings.weekly.confirm_bars}."
        )
    else:
        setup_state = "NEUTRAL"
        reason = (
            f"Histogram {hist_prev:+.4f} -> {hist_now:+.4f} (unclear direction); "
            f"13EMA slope {ema_delta:+.4f}; confirmed bars {confirmed}/{settings.weekly.confirm_bars}."
        )

    return {
        "trend": trend,
        "impulse": impulse,
        "impulse_color": "GREEN" if trend == "LONG" else "RED" if trend == "SHORT" else "BLUE",
        "impulse_direction": trend,
        "allows_long": trend != "SHORT",
        "allows_short": trend != "LONG",
        "setup_state": setup_state,
        "histogram": round(hist_now, 6),
        "histogram_prev": round(hist_prev, 6),
        "histogram_delta": round(hist_delta, 6),
        "histogram_strength": abs(hist_now),
        "histogram_growing": hist_delta > 0,
        "macd": round(float(macd.iloc[-1]), 6),
        "macd_signal": round(float(signal.iloc[-1]), 6),
        "ema13": round(ema_now, 6),
        "ema13_prev": round(ema_prev, 6),
        "ema13_slope": round(ema_delta, 6),
        "confirmed_bars": confirmed,
        "impulse_aligned": impulse_aligned,
        "impulse_allows_direction": impulse_aligned,
        "close_on_trend_side": close_on_trend_side,
        "weekly_value_target": indicators.calc_weekly_value_target(df_week, trend),
        "trend_score": round(min(trend_score, indicators.WEEKLY_TREND_SCORE_CAP), 2),
        "actionable": actionable,
        "pass_checks": {
            "actionable": actionable,
            "confirmed_bars": confirmed_pass,
            "impulse_aligned": impulse_aligned or not settings.weekly.require_impulse_alignment,
            "close_on_trend_side": close_on_trend_side,
        },
        "pass": (
            actionable
            and confirmed_pass
            and (impulse_aligned or not settings.weekly.require_impulse_alignment)
            and close_on_trend_side
        ),
        "reason": reason,
    }


def legacy_screen_daily(df_day: pd.DataFrame | None, trend: str, settings: StrategyConfig) -> dict:
    if df_day is None or len(df_day) < settings.daily.rsi_period + 5:
        return {
            "pass": False,
            "watch": False,
            "state": "REJECT",
            "reject_reason": "Insufficient daily data",
            "reason": "Insufficient daily data",
            "countertrend_exists": False,
            "value_zone_reached": False,
            "reversal_evidence_count": 0,
            "structure_intact": False,
            "priority_divergence": False,
            "earnings_blocked": False,
        }

    rsi = indicators.calc_rsi(df_day, settings.daily.rsi_period)
    rsi_now = float(rsi.iloc[-1])
    rsi_prev = float(rsi.iloc[-2])
    close = df_day["close"].astype(float)
    high = df_day["high"].astype(float)
    low = df_day["low"].astype(float)
    ema13 = indicators.calc_ema(close, indicators.DAILY_EMA_PERIOD)
    atr_series = indicators.calc_atr(df_day, settings.hourly.atr_period)
    value_band_low, value_band_high, value_band_padding = indicators.calc_value_zone_bounds(
        ema13,
        atr_series,
        settings.daily.value_band_atr_multiplier,
    )
    _, _, macd_hist = indicators.calc_macd(df_day, settings)

    recent = df_day.tail(indicators.DAILY_CORRECTION_WINDOW_MAX).copy()
    recent_close = recent["close"].astype(float)
    recent_high = recent["high"].astype(float)
    recent_low = recent["low"].astype(float)
    recent_ema = ema13.tail(indicators.DAILY_CORRECTION_WINDOW_MAX)
    recent_value_band_low = value_band_low.tail(indicators.DAILY_CORRECTION_WINDOW_MAX)
    recent_value_band_high = value_band_high.tail(indicators.DAILY_CORRECTION_WINDOW_MAX)
    prior_closes = close.iloc[-indicators.DAILY_CORRECTION_WINDOW_MAX :]
    latest_open = float(df_day["open"].iloc[-1])
    latest_close = float(close.iloc[-1])
    latest_high = float(high.iloc[-1])
    latest_low = float(low.iloc[-1])
    macd_hist_now = float(macd_hist.iloc[-1])
    macd_hist_prev = float(macd_hist.iloc[-2])
    rsi_delta = rsi_now - rsi_prev
    macd_hist_delta = macd_hist_now - macd_hist_prev

    down_closes = int((prior_closes.diff() < 0).sum())
    up_closes = int((prior_closes.diff() > 0).sum())
    latest_gap_window = None
    value_zone_touch = bool(
        ((recent_low <= recent_value_band_high) & (recent_high >= recent_value_band_low))
        .tail(indicators.DAILY_REVERSAL_LOOKBACK)
        .any()
    )

    correction_bar_count = min(len(recent_close), indicators.DAILY_CORRECTION_WINDOW_MAX)
    correction_in_window = correction_bar_count >= indicators.DAILY_CORRECTION_WINDOW_MIN
    rsi_state = "NEUTRAL"
    state = "WATCH"
    reject_reason = ""
    passed = False
    watch = False
    correction_count = 0
    correction_counter_label = "recent correction closes"
    structure_break_level = None
    custom_close_rule_pass = False
    custom_wick_rule_pass = False
    custom_close_location_rule_pass = False
    close_above_prev = False
    close_below_prev = False
    close_location_pct = 0.0
    wick_ratio_pct = 0.0
    histogram_reversal = False
    custom_kline_confirmation = False
    countertrend_exists = False
    entered_value_zone = False
    value_zone_reached = False
    structure_intact = False
    elder_core_checks = [False, False, False]
    rsi_strength = 0.0
    setup_score = 0.0

    if trend == "LONG":
        correction_count = down_closes
        correction_counter_label = "recent down closes"
        recent_gap_to_value_band = (recent_close - recent_value_band_high).clip(lower=0)
        latest_gap_window = recent_gap_to_value_band.tail(indicators.DAILY_REVERSAL_LOOKBACK)
        countertrend_exists = correction_in_window and (down_closes >= 2 or bool((recent_close <= recent_ema).any()))
        value_zone_approach = bool(
            len(latest_gap_window) >= 2
            and close.iloc[-1] >= value_band_low.iloc[-1]
            and latest_gap_window.iloc[-1] == latest_gap_window.min()
            and latest_gap_window.iloc[-1] < latest_gap_window.iloc[-2]
            and (close.iloc[-1] <= close.iloc[-2] or low.iloc[-1] <= low.iloc[-2])
        )
        entered_value_zone = value_zone_touch or value_zone_approach
        value_zone_reached = entered_value_zone
        higher_low_ref = float(low.tail(indicators.DAILY_CORRECTION_WINDOW_MAX).min())
        structure_break_level = higher_low_ref - (float(atr_series.iloc[-1]) * indicators.DAILY_STRUCTURE_BREACH_ATR_MULTIPLIER)
        structure_intact = bool(low.iloc[-1] >= structure_break_level)
        lower_wick = float(min(latest_close, latest_open) - latest_low)
        candle_range = max(float(latest_high - latest_low), 1e-9)
        close_location_pct = ((latest_close - latest_low) / candle_range) * 100
        upper_half_close = latest_close >= (latest_low + candle_range * 0.5)
        close_above_prev = bool(latest_close > close.iloc[-2])
        wick_ratio_pct = (lower_wick / candle_range) * 100
        histogram_reversal = bool(macd_hist_now > macd_hist_prev)
        custom_close_rule_pass = close_above_prev
        custom_wick_rule_pass = bool(lower_wick >= candle_range * 0.35)
        custom_close_location_rule_pass = upper_half_close
        custom_kline_confirmation = bool(custom_close_rule_pass and custom_wick_rule_pass and custom_close_location_rule_pass)
        accelerating_correction = bool(close.iloc[-1] < close.iloc[-2] < close.iloc[-3] and macd_hist_now < macd_hist_prev)
        rsi_strength = max(0.0, 45.0 - rsi_now)
        elder_core_checks = [value_zone_reached, histogram_reversal, structure_intact]
        setup_score = (
            1.3
            + (1.0 if value_zone_reached else 0.0)
            + (1.5 if histogram_reversal else 0.0)
            + (1.0 if structure_intact else 0.0)
            + (0.2 if custom_kline_confirmation else 0.0)
        )
        if not countertrend_exists:
            state = "REJECT"
            reject_reason = "Weekly trend is long, but daily has no recognizable pullback setup"
            rsi_state = "NO_PULLBACK"
        elif not structure_intact:
            state = "REJECT"
            reject_reason = "Pullback structure broke below the defensive pivot, so stop boundary is not well-defined"
            rsi_state = "STRUCTURE_BROKEN"
        elif accelerating_correction and not histogram_reversal:
            state = "REJECT"
            reject_reason = "Daily pullback is still accelerating without evidence of downside deceleration"
            rsi_state = "ACCELERATING_PULLBACK"
        elif not entered_value_zone:
            state = "WATCH"
            watch = True
            rsi_state = "PULLBACK_WAIT_VALUE_BAND"
        elif histogram_reversal:
            state = "QUALIFIED"
            passed = True
            rsi_state = "PULLBACK_HISTOGRAM_TURNED"
        else:
            state = "WATCH"
            watch = True
            rsi_state = "PULLBACK_WAIT_HISTOGRAM"
    elif trend == "SHORT":
        correction_count = up_closes
        correction_counter_label = "recent up closes"
        recent_gap_to_value_band = (recent_value_band_low - recent_close).clip(lower=0)
        latest_gap_window = recent_gap_to_value_band.tail(indicators.DAILY_REVERSAL_LOOKBACK)
        countertrend_exists = correction_in_window and (up_closes >= 2 or bool((recent_close >= recent_ema).any()))
        value_zone_approach = bool(
            len(latest_gap_window) >= 2
            and close.iloc[-1] <= value_band_high.iloc[-1]
            and latest_gap_window.iloc[-1] == latest_gap_window.min()
            and latest_gap_window.iloc[-1] < latest_gap_window.iloc[-2]
            and (close.iloc[-1] >= close.iloc[-2] or high.iloc[-1] >= high.iloc[-2])
        )
        entered_value_zone = value_zone_touch or value_zone_approach
        value_zone_reached = entered_value_zone
        lower_high_ref = float(high.tail(indicators.DAILY_CORRECTION_WINDOW_MAX).max())
        structure_break_level = lower_high_ref + (float(atr_series.iloc[-1]) * indicators.DAILY_STRUCTURE_BREACH_ATR_MULTIPLIER)
        structure_intact = bool(high.iloc[-1] <= structure_break_level)
        upper_wick = float(latest_high - max(latest_close, latest_open))
        candle_range = max(float(latest_high - latest_low), 1e-9)
        close_location_pct = ((latest_close - latest_low) / candle_range) * 100
        lower_half_close = latest_close <= (latest_high - candle_range * 0.5)
        close_below_prev = bool(latest_close < close.iloc[-2])
        wick_ratio_pct = (upper_wick / candle_range) * 100
        histogram_reversal = bool(macd_hist_now < macd_hist_prev)
        custom_close_rule_pass = close_below_prev
        custom_wick_rule_pass = bool(upper_wick >= candle_range * 0.35)
        custom_close_location_rule_pass = lower_half_close
        custom_kline_confirmation = bool(custom_close_rule_pass and custom_wick_rule_pass and custom_close_location_rule_pass)
        accelerating_correction = bool(close.iloc[-1] > close.iloc[-2] > close.iloc[-3] and macd_hist_now > macd_hist_prev)
        rsi_strength = max(0.0, rsi_now - 55.0)
        elder_core_checks = [value_zone_reached, histogram_reversal, structure_intact]
        setup_score = (
            1.3
            + (1.0 if value_zone_reached else 0.0)
            + (1.5 if histogram_reversal else 0.0)
            + (1.0 if structure_intact else 0.0)
            + (0.2 if custom_kline_confirmation else 0.0)
        )
        if not countertrend_exists:
            state = "REJECT"
            reject_reason = "Weekly trend is short, but daily has no recognizable rally setup"
            rsi_state = "NO_RALLY"
        elif not structure_intact:
            state = "REJECT"
            reject_reason = "Rally structure broke above the defensive pivot, so stop boundary is not well-defined"
            rsi_state = "STRUCTURE_BROKEN"
        elif accelerating_correction and not histogram_reversal:
            state = "REJECT"
            reject_reason = "Daily rally is still accelerating without evidence of stalling weakness"
            rsi_state = "ACCELERATING_RALLY"
        elif not entered_value_zone:
            state = "WATCH"
            watch = True
            rsi_state = "RALLY_WAIT_VALUE_BAND"
        elif histogram_reversal:
            state = "QUALIFIED"
            passed = True
            rsi_state = "RALLY_HISTOGRAM_TURNED"
        else:
            state = "WATCH"
            watch = True
            rsi_state = "RALLY_WAIT_HISTOGRAM"
    else:
        reject_reason = "Weekly direction is unclear; daily setup is not traded independently"

    elder_core_signal_count = int(sum(elder_core_checks))
    direction_label = "pullback" if trend == "LONG" else "rally" if trend == "SHORT" else "correction"
    value_zone_label = (
        f"{value_band_low.iloc[-1]:.2f}~{value_band_high.iloc[-1]:.2f}, latest range {latest_low:.2f}~{latest_high:.2f}"
        if trend in {"LONG", "SHORT"}
        else "—"
    )
    structure_label = (
        f"Latest low {latest_low:.2f} >= defense level {structure_break_level:.2f}"
        if trend == "LONG" and structure_break_level is not None
        else f"Latest high {latest_high:.2f} <= defense level {structure_break_level:.2f}"
        if trend == "SHORT" and structure_break_level is not None
        else "—"
    )
    histogram_label = f"Histogram {macd_hist_prev:+.4f}->{macd_hist_now:+.4f}" if trend in {"LONG", "SHORT"} else "—"
    detail_prefix = (
        f"{correction_counter_label} {correction_count}; "
        f"13EMA value band {value_zone_label}; "
        f"{structure_label}; "
        f"Histogram check {histogram_label}."
    )
    if state == "REJECT":
        reason = f"{detail_prefix} Conclusion: {reject_reason}"
    elif state == "QUALIFIED":
        reason = f"{detail_prefix} Conclusion: {direction_label} setup is executable; {elder_core_signal_count}/3 Elder core signals are ready; candidate can enter the pool."
    else:
        reason = (
            f"{detail_prefix} Conclusion: {direction_label} setup exists, but only {elder_core_signal_count}/3 Elder core signals are ready; keep watching."
            if value_zone_reached
            else f"{detail_prefix} Conclusion: {direction_label} setup exists, but price has not returned to the 13EMA value band; keep watching."
        )

    return {
        "rsi": round(rsi_now, 2),
        "rsi_prev": round(rsi_prev, 2),
        "rsi_state": rsi_state,
        "rsi_strength": round(rsi_strength, 2),
        "setup_score": round(min(setup_score, indicators.DAILY_SETUP_SCORE_CAP), 2),
        "state": state,
        "reject_reason": reject_reason or None,
        "countertrend_exists": countertrend_exists,
        "entered_value_zone": entered_value_zone if trend in {"LONG", "SHORT"} else False,
        "value_zone_reached": value_zone_reached,
        "value_band_low": round(float(value_band_low.iloc[-1]), 4) if trend in {"LONG", "SHORT"} else None,
        "value_band_high": round(float(value_band_high.iloc[-1]), 4) if trend in {"LONG", "SHORT"} else None,
        "value_band_padding": round(float(value_band_padding.iloc[-1]), 4) if trend in {"LONG", "SHORT"} else None,
        "value_band_gap": round(float(latest_gap_window.iloc[-1]), 4) if latest_gap_window is not None and len(latest_gap_window) else None,
        "correction_count": correction_count if trend in {"LONG", "SHORT"} else 0,
        "correction_counter_label": correction_counter_label,
        "elder_core_signal_count": elder_core_signal_count,
        "elder_core_signal_total": 3,
        "structure_intact": structure_intact,
        "structure_break_level": round(float(structure_break_level), 4) if structure_break_level is not None else None,
        "histogram_reversal": histogram_reversal if trend in {"LONG", "SHORT"} else False,
        "momentum_reversal": histogram_reversal if trend in {"LONG", "SHORT"} else False,
        "momentum_rsi_delta": round(float(rsi_delta), 2) if trend in {"LONG", "SHORT"} else 0.0,
        "momentum_hist_now": round(macd_hist_now, 6) if trend in {"LONG", "SHORT"} else 0.0,
        "momentum_hist_prev": round(macd_hist_prev, 6) if trend in {"LONG", "SHORT"} else 0.0,
        "momentum_hist_delta": round(float(macd_hist_delta), 6) if trend in {"LONG", "SHORT"} else 0.0,
        "price_reversal": custom_kline_confirmation if trend in {"LONG", "SHORT"} else False,
        "custom_kline_confirmation": custom_kline_confirmation if trend in {"LONG", "SHORT"} else False,
        "custom_close_vs_prev": close_above_prev if trend == "LONG" else close_below_prev if trend == "SHORT" else False,
        "custom_close_rule_pass": custom_close_rule_pass if trend in {"LONG", "SHORT"} else False,
        "custom_wick_rule_pass": custom_wick_rule_pass if trend in {"LONG", "SHORT"} else False,
        "custom_close_location_rule_pass": custom_close_location_rule_pass if trend in {"LONG", "SHORT"} else False,
        "custom_wick_ratio_pct": round(wick_ratio_pct, 2) if trend in {"LONG", "SHORT"} else 0.0,
        "custom_close_location_pct": round(close_location_pct, 2) if trend in {"LONG", "SHORT"} else 0.0,
        "priority_divergence": False,
        "earnings_blocked": False,
        "watch": watch,
        "pass": passed,
        "reason": reason,
    }


def legacy_calc_exits(
    direction: str,
    entry: float,
    daily_frame: pd.DataFrame | None,
    atr: float,
    trade_plan: TradePlanConfig,
    signal_bar_high: float | None = None,
    signal_bar_low: float | None = None,
) -> dict:
    if daily_frame is None or daily_frame.empty:
        stop_loss = None
        initial_stop_loss = None
        protective_stop_loss = entry
        take_profit = entry
        thermometer = 0.0
        thermometer_ema = 0.0
        safezone_stop = entry
        nick_stop = entry
        atr_stop_1x = entry
        atr_stop_2x = entry
        daily_atr = 0.0
        stop_methods = []
        stop_basis = "CHOICE_REQUIRED"
        initial_stop_basis = "CHOICE_REQUIRED"
        protective_stop_basis = "UNKNOWN"
        target_reference = entry
        safezone_noise = 0.0
        model_initial_stop_loss = entry
        model_initial_stop_basis = "UNKNOWN"
    else:
        latest_high = float(daily_frame["high"].iloc[-1])
        latest_low = float(daily_frame["low"].iloc[-1])
        safezone_stop, safezone_noise = indicators.calc_safezone_stop(daily_frame, direction, trade_plan)
        nick_stop = indicators.calc_nick_stop(daily_frame, direction)
        atr_stops, daily_atr = indicators.calc_atr_stops(daily_frame, direction, atr_period=14)
        atr_stop_1x = atr_stops.get(1.0)
        atr_stop_2x = atr_stops.get(2.0)
        stop_methods = indicators.build_stop_methods(
            daily_frame,
            direction,
            trade_plan,
            atr_period=14,
            signal_bar_high=signal_bar_high,
            signal_bar_low=signal_bar_low,
        )
        temperature, average_temperature = indicators.calc_market_thermometer(daily_frame, trade_plan.thermometer_period)
        thermometer = float(temperature.iloc[-1])
        thermometer_ema = float(average_temperature.iloc[-1])
        projected_move = thermometer_ema * trade_plan.thermometer_target_multiplier
        initial_candidates = [("SAFEZONE", safezone_stop), ("NICK", nick_stop)]
        valid_initial_candidates = [(code, float(value)) for code, value in initial_candidates if value is not None]
        if direction == "LONG":
            if valid_initial_candidates:
                model_initial_stop_basis, model_initial_stop_loss = min(valid_initial_candidates, key=lambda item: item[1])
            else:
                model_initial_stop_basis, model_initial_stop_loss = "UNKNOWN", entry
            target_reference = max(entry, latest_high)
            take_profit = target_reference + projected_move
        else:
            if valid_initial_candidates:
                model_initial_stop_basis, model_initial_stop_loss = max(valid_initial_candidates, key=lambda item: item[1])
            else:
                model_initial_stop_basis, model_initial_stop_loss = "UNKNOWN", entry
            target_reference = min(entry, latest_low)
            take_profit = target_reference - projected_move
        initial_stop_basis = "CHOICE_REQUIRED"
        initial_stop_loss = None
        protective_stop_loss = atr_stop_1x if atr_stop_1x is not None else model_initial_stop_loss
        protective_stop_basis = "ATR_1X" if atr_stop_1x is not None else model_initial_stop_basis
        stop_loss = None
        stop_basis = "CHOICE_REQUIRED"

    model_risk_per_share = abs(entry - model_initial_stop_loss) if model_initial_stop_loss is not None else 0.0
    reward_per_share = abs(take_profit - entry)
    model_reward_risk = (reward_per_share / model_risk_per_share) if model_risk_per_share > 0 else 0.0
    return {
        "entry": round(entry, 4),
        "stop_loss": round(stop_loss, 4) if stop_loss is not None else None,
        "initial_stop_loss": round(initial_stop_loss, 4) if initial_stop_loss is not None else None,
        "initial_stop_signal_bar": None,
        "initial_stop_two_bar": None,
        "initial_stop_safezone": round(safezone_stop, 4) if safezone_stop is not None else None,
        "initial_stop_nick": round(nick_stop, 4) if nick_stop is not None else None,
        "initial_stop_pullback_pivot": None,
        "initial_stop_basis": initial_stop_basis,
        "initial_stop_model_loss": round(model_initial_stop_loss, 4) if model_initial_stop_loss is not None else None,
        "initial_stop_model_basis": model_initial_stop_basis,
        "protective_stop_loss": round(protective_stop_loss, 4),
        "protective_stop_basis": protective_stop_basis,
        "stop_loss_safezone": round(safezone_stop, 4) if safezone_stop is not None else None,
        "stop_loss_two_bar": None,
        "stop_loss_nick": round(nick_stop, 4) if nick_stop is not None else None,
        "stop_loss_atr_1x": round(atr_stop_1x, 4) if atr_stop_1x is not None else None,
        "stop_loss_atr_2x": round(atr_stop_2x, 4) if atr_stop_2x is not None else None,
        "stop_loss_chandelier": None,
        "stop_loss_parabolic": None,
        "stop_methods": stop_methods,
        "stop_basis": stop_basis,
        "take_profit": round(take_profit, 4),
        "target_reference": round(target_reference, 4),
        "weekly_value_target": {"available": False, "target_price": None},
        "thermometer": round(thermometer, 4),
        "thermometer_ema": round(thermometer_ema, 4),
        "daily_atr": round(daily_atr, 4),
        "safezone_noise": round(safezone_noise, 4),
        "risk_per_share": None,
        "risk_per_share_model": round(model_risk_per_share, 4),
        "reward_per_share": round(reward_per_share, 4),
        "reward_risk_ratio": None,
        "reward_risk_ratio_model": round(model_reward_risk, 2),
        "atr": round(atr, 4),
        "exit_timeframe": "DAY",
    }


_MODELS: dict[str, TradingModel] = {
    ELDER_FORCE_MODEL_ID: TradingModel(
        spec=TradingModelSpec(
            id=ELDER_FORCE_MODEL_ID,
            label="Elder Force pullback model",
            description="Elder triple-screen style model: weekly MACD slope plus impulse-system direction gate, daily 2-day Force Index for trend-aligned pullbacks/rallies, and intraday execution using daily EMA penetration first with previous-day breakout as the alternate trigger.",
            weekly_model="Elder impulse-system gate with MACD slope",
            daily_model="Elder 2-day Force Index pullback/rally setup",
            intraday_trigger="Daily EMA penetration entry first, previous-day break as alternate trigger",
            exit_model="SafeZone/Nick initial stop; weekly value-zone target when available; daily ATR 1x trailing stop",
        ),
        screen_weekly_fn=indicators.screen_weekly,
        screen_daily_fn=indicators.screen_daily,
        use_weekly_value_target=True,
        use_planned_daily_entry=True,
    ),
    VALUE_REVERSAL_MODEL_ID: TradingModel(
        spec=TradingModelSpec(
            id=VALUE_REVERSAL_MODEL_ID,
            label="Value-band reversal model",
            description="Conservative value-band pullback model: weekly MACD histogram and 13EMA alignment, daily return to the 13EMA value band with histogram reversal and intact structure, then intraday trigger from a trailing stop at the previous closed hourly bar high/low.",
            weekly_model="MACD histogram delta with EMA13 alignment",
            daily_model="Value-zone pullback/rally plus histogram reversal",
            intraday_trigger="Trailing buy-stop/sell-stop from the previous closed hourly bar",
            exit_model="SafeZone/Nick initial stop; thermometer target; daily ATR 1x trailing stop",
        ),
        screen_weekly_fn=legacy_screen_weekly,
        screen_daily_fn=legacy_screen_daily,
        use_weekly_value_target=False,
        use_planned_daily_entry=False,
    ),
}


def list_models() -> list[dict[str, Any]]:
    return [model.to_dict() for model in _MODELS.values()]


def list_model_ids(include_aliases: bool = False) -> list[str]:
    ids = list(_MODELS)
    if include_aliases:
        ids.extend(MODEL_ID_ALIASES)
    return ids


def normalize_model_id(model_id: str | None) -> str:
    value = (model_id or DEFAULT_MODEL_ID).strip()
    value = MODEL_ID_ALIASES.get(value, value)
    return value if value in _MODELS else DEFAULT_MODEL_ID


def get_model(model_id: str | None = None) -> TradingModel:
    return _MODELS[normalize_model_id(model_id)]
