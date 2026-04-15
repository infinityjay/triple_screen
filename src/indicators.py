from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from config.schema import StrategyConfig, TradePlanConfig

DAILY_REVERSAL_LOOKBACK = 3
DAILY_CORRECTION_WINDOW_MIN = 2
DAILY_CORRECTION_WINDOW_MAX = 8
DAILY_EMA_PERIOD = 13
DAILY_STRUCTURE_BREACH_ATR_MULTIPLIER = 0.3
WEEKLY_TREND_SCORE_CAP = 4.5
DAILY_SETUP_SCORE_CAP = 4.5
HOURLY_TRIGGER_SCORE_CAP = 4.0
REWARD_RISK_SCORE_CAP = 2.0
DIVERGENCE_LOOKBACK = 40
PIVOT_ORDER = 2


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(df: pd.DataFrame, settings: StrategyConfig) -> tuple[pd.Series, pd.Series, pd.Series]:
    close = df["close"]
    macd = calc_ema(close, settings.weekly.macd_fast) - calc_ema(close, settings.weekly.macd_slow)
    signal = calc_ema(macd, settings.weekly.macd_signal)
    histogram = macd - signal
    return macd, signal, histogram


def calc_rsi(df: pd.DataFrame, period: int) -> pd.Series:
    close = df["close"]
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def calc_market_thermometer(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series]:
    high = df["high"]
    low = df["low"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    upside_extension = (high - prev_high).clip(lower=0)
    downside_extension = (prev_low - low).clip(lower=0)
    temperature = pd.concat([upside_extension, downside_extension], axis=1).max(axis=1).fillna(0.0)
    average_temperature = temperature.ewm(span=period, adjust=False).mean()
    return temperature, average_temperature


def calc_value_zone_bounds(
    ema_series: pd.Series,
    atr_series: pd.Series,
    atr_multiplier: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    padding = atr_series.abs() * max(float(atr_multiplier), 0.0)
    lower_band = ema_series - padding
    upper_band = ema_series + padding
    return lower_band, upper_band, padding


def calc_reward_risk_score(reward_risk_ratio: float) -> float:
    if reward_risk_ratio <= 0:
        return 0.0
    if reward_risk_ratio < 1.0:
        return round(reward_risk_ratio * 0.8, 2)
    if reward_risk_ratio < 1.5:
        return round(0.8 + ((reward_risk_ratio - 1.0) * 1.2), 2)
    if reward_risk_ratio < 2.0:
        return round(1.4 + ((reward_risk_ratio - 1.5) * 0.8), 2)
    return round(min(1.8 + ((reward_risk_ratio - 2.0) * 0.2), REWARD_RISK_SCORE_CAP), 2)


def _find_pivots(series: pd.Series, mode: str, order: int = PIVOT_ORDER) -> list[int]:
    if len(series) < order * 2 + 1:
        return []

    values = series.astype(float).tolist()
    pivots: list[int] = []
    for index in range(order, len(values) - order):
        center = values[index]
        left = values[index - order : index]
        right = values[index + 1 : index + order + 1]
        if mode == "high":
            if center >= max(left) and center > max(right):
                pivots.append(index)
        else:
            if center <= min(left) and center < min(right):
                pivots.append(index)
    return pivots


def _nearest_pivot(target_index: int, pivots: list[int], max_distance: int = 3) -> int | None:
    nearest: int | None = None
    distance = max_distance + 1
    for pivot in pivots:
        pivot_distance = abs(pivot - target_index)
        if pivot_distance <= max_distance and pivot_distance < distance:
            nearest = pivot
            distance = pivot_distance
    return nearest


def detect_divergence(
    df: pd.DataFrame | None,
    settings: StrategyConfig,
    direction: str,
    timeframe: str,
    exhaustion_multiplier: float,
) -> dict:
    if df is None or len(df) < max(DIVERGENCE_LOOKBACK, 25):
        return {
            "detected": False,
            "strong_alert": False,
            "timeframe": timeframe,
            "direction": direction,
            "reason": f"{timeframe}数据不足，无法判断背离",
        }

    frame = df.tail(DIVERGENCE_LOOKBACK).copy()
    _, _, histogram = calc_macd(frame, settings)
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)

    price_pivots = _find_pivots(high if direction == "SHORT" else low, "high" if direction == "SHORT" else "low")
    hist_pivots = _find_pivots(histogram, "high" if direction == "SHORT" else "low")
    if len(price_pivots) < 2 or len(hist_pivots) < 2:
        return {
            "detected": False,
            "strong_alert": False,
            "timeframe": timeframe,
            "direction": direction,
            "reason": f"{timeframe}未形成足够清晰的摆点背离",
        }

    second_price_pivot = price_pivots[-1]
    first_price_pivot = price_pivots[-2]
    first_hist_pivot = _nearest_pivot(first_price_pivot, hist_pivots)
    second_hist_pivot = _nearest_pivot(second_price_pivot, hist_pivots)
    if first_hist_pivot is None or second_hist_pivot is None:
        return {
            "detected": False,
            "strong_alert": False,
            "timeframe": timeframe,
            "direction": direction,
            "reason": f"{timeframe}价格摆点附近缺少MACD柱线摆点",
        }

    if direction == "SHORT":
        price_condition = high.iloc[second_price_pivot] > high.iloc[first_price_pivot]
        histogram_condition = histogram.iloc[second_hist_pivot] < histogram.iloc[first_hist_pivot]
        crossed_zero = bool((histogram.iloc[first_hist_pivot:second_hist_pivot] < 0).any())
        label = "熊市顶背离"
    else:
        price_condition = low.iloc[second_price_pivot] < low.iloc[first_price_pivot]
        histogram_condition = histogram.iloc[second_hist_pivot] > histogram.iloc[first_hist_pivot]
        crossed_zero = bool((histogram.iloc[first_hist_pivot:second_hist_pivot] > 0).any())
        label = "牛市底背离"

    detected = bool(price_condition and histogram_condition and crossed_zero)
    if not detected:
        return {
            "detected": False,
            "strong_alert": False,
            "timeframe": timeframe,
            "direction": direction,
            "reason": f"{timeframe}最近两次摆点未满足{label}条件",
        }

    strong_alert = False
    exhaustion_reason = "未出现显著的三柱衰竭形态"
    if len(frame) >= 23:
        ranges = (frame["high"] - frame["low"]).astype(float)
        recent_three = ranges.iloc[-3:]
        baseline = float(ranges.iloc[-23:-3].median()) if len(ranges.iloc[-23:-3]) > 0 else float(ranges.iloc[:-3].median())
        middle_bar = recent_three.iloc[1]
        if baseline > 0 and middle_bar >= baseline * exhaustion_multiplier:
            middle = frame.iloc[-2]
            if direction == "SHORT":
                strong_alert = bool(
                    middle["high"] >= frame["high"].iloc[-3:].max()
                    and middle["close"] <= (middle["high"] + middle["low"]) / 2
                )
            else:
                strong_alert = bool(
                    middle["low"] <= frame["low"].iloc[-3:].min()
                    and middle["close"] >= (middle["high"] + middle["low"]) / 2
                )
            if strong_alert:
                exhaustion_reason = (
                    f"最近三根K线中间一根振幅达到近20根中位数的 {middle_bar / baseline:.2f} 倍"
                )

    return {
        "detected": True,
        "strong_alert": strong_alert,
        "timeframe": timeframe,
        "direction": direction,
        "label": label,
        "first_price_pivot_at": str(frame.index[first_price_pivot]),
        "second_price_pivot_at": str(frame.index[second_price_pivot]),
        "reason": f"{timeframe}{label}成立，且柱线在两次摆点之间完成零轴穿越",
        "exhaustion_reason": exhaustion_reason,
    }


def calc_safezone_stop(df: pd.DataFrame, direction: str, plan: TradePlanConfig) -> tuple[float | None, float]:
    if df is None or len(df) < 2:
        return None, 0.0

    lookback = max(plan.safezone_lookback, 1)
    if direction == "LONG":
        penetrations = (df["low"].shift(1) - df["low"]).clip(lower=0).dropna()
        reference_price = float(df["low"].iloc[-1])
        average_penetration = float(penetrations.tail(lookback).mean()) if not penetrations.empty else 0.0
        stop = reference_price - (average_penetration * plan.safezone_coefficient)
    else:
        penetrations = (df["high"] - df["high"].shift(1)).clip(lower=0).dropna()
        reference_price = float(df["high"].iloc[-1])
        average_penetration = float(penetrations.tail(lookback).mean()) if not penetrations.empty else 0.0
        stop = reference_price + (average_penetration * plan.safezone_coefficient)

    return stop, average_penetration


def calc_pullback_pivot_stop(df: pd.DataFrame, direction: str) -> float | None:
    if df is None or df.empty:
        return None

    window = df.tail(DAILY_CORRECTION_WINDOW_MAX)
    if direction == "LONG":
        return float(window["low"].min())
    return float(window["high"].max())


def screen_weekly(df_week: pd.DataFrame | None, settings: StrategyConfig) -> dict:
    required = settings.weekly.macd_slow + settings.weekly.macd_signal + 5
    if df_week is None or len(df_week) < required:
        return {"trend": "NEUTRAL", "pass": False, "actionable": False, "reason": "周线数据不足"}

    macd, signal, histogram = calc_macd(df_week, settings)
    close = df_week["close"].astype(float)
    ema13 = calc_ema(df_week["close"].astype(float), DAILY_EMA_PERIOD)
    hist_now = histogram.iloc[-1]
    hist_prev = histogram.iloc[-2]
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
        or (trend == "NEUTRAL")
    )
    close_on_trend_side = (
        (trend == "LONG" and close_now > ema_now)
        or (trend == "SHORT" and close_now < ema_now)
        or (trend == "NEUTRAL")
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
            f"柱线 {hist_prev:+.4f} -> {hist_now:+.4f}（回升）；"
            f"13EMA 斜率 {ema_delta:+.4f}（{'上行' if ema_delta > 0 else '未上行'}）；"
            f"确认 bars {confirmed}/{settings.weekly.confirm_bars}。"
        )
    elif trend == "SHORT":
        setup_state = "BEARISH_SLOPE"
        reason = (
            f"柱线 {hist_prev:+.4f} -> {hist_now:+.4f}（回落）；"
            f"13EMA 斜率 {ema_delta:+.4f}（{'下行' if ema_delta < 0 else '未下行'}）；"
            f"确认 bars {confirmed}/{settings.weekly.confirm_bars}。"
        )
    else:
        setup_state = "NEUTRAL"
        reason = (
            f"柱线 {hist_prev:+.4f} -> {hist_now:+.4f}（方向不清晰）；"
            f"13EMA 斜率 {ema_delta:+.4f}；确认 bars {confirmed}/{settings.weekly.confirm_bars}。"
        )

    return {
        "trend": trend,
        "impulse": impulse,
        "setup_state": setup_state,
        "histogram": round(float(hist_now), 6),
        "histogram_prev": round(float(hist_prev), 6),
        "histogram_delta": round(float(hist_delta), 6),
        "histogram_strength": abs(float(hist_now)),
        "histogram_growing": hist_delta > 0,
        "macd": round(float(macd.iloc[-1]), 6),
        "macd_signal": round(float(signal.iloc[-1]), 6),
        "ema13": round(ema_now, 6),
        "ema13_prev": round(ema_prev, 6),
        "ema13_slope": round(float(ema_delta), 6),
        "confirmed_bars": confirmed,
        "impulse_aligned": impulse_aligned,
        "close_on_trend_side": close_on_trend_side,
        "trend_score": round(min(trend_score, WEEKLY_TREND_SCORE_CAP), 2),
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


def screen_daily(df_day: pd.DataFrame | None, trend: str, settings: StrategyConfig) -> dict:
    if df_day is None or len(df_day) < settings.daily.rsi_period + 5:
        return {
            "pass": False,
            "watch": False,
            "state": "REJECT",
            "reject_reason": "日线数据不足",
            "reason": "日线数据不足",
            "countertrend_exists": False,
            "value_zone_reached": False,
            "reversal_evidence_count": 0,
            "structure_intact": False,
            "priority_divergence": False,
            "earnings_blocked": False,
        }

    rsi = calc_rsi(df_day, settings.daily.rsi_period)
    rsi_now = float(rsi.iloc[-1])
    rsi_prev = float(rsi.iloc[-2])
    close = df_day["close"].astype(float)
    high = df_day["high"].astype(float)
    low = df_day["low"].astype(float)
    ema13 = calc_ema(close, DAILY_EMA_PERIOD)
    atr_series = calc_atr(df_day, settings.hourly.atr_period)
    value_band_low, value_band_high, value_band_padding = calc_value_zone_bounds(
        ema13,
        atr_series,
        settings.daily.value_band_atr_multiplier,
    )
    _, _, macd_hist = calc_macd(df_day, settings)

    recent = df_day.tail(DAILY_CORRECTION_WINDOW_MAX).copy()
    recent_close = recent["close"].astype(float)
    recent_high = recent["high"].astype(float)
    recent_low = recent["low"].astype(float)
    recent_ema = ema13.tail(DAILY_CORRECTION_WINDOW_MAX)
    recent_value_band_low = value_band_low.tail(DAILY_CORRECTION_WINDOW_MAX)
    recent_value_band_high = value_band_high.tail(DAILY_CORRECTION_WINDOW_MAX)
    lookback_slice = slice(-DAILY_CORRECTION_WINDOW_MAX, None)
    prior_closes = close.iloc[lookback_slice]
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
        .tail(DAILY_REVERSAL_LOOKBACK)
        .any()
    )

    correction_bar_count = min(len(recent_close), DAILY_CORRECTION_WINDOW_MAX)
    correction_in_window = correction_bar_count >= DAILY_CORRECTION_WINDOW_MIN
    rsi_state = "NEUTRAL"
    state = "WATCH"
    reject_reason = ""
    passed = False
    watch = False
    correction_count = 0
    correction_counter_label = "近8日修正收盘数"
    structure_break_level = None
    custom_close_rule_pass = False
    custom_wick_rule_pass = False
    custom_close_location_rule_pass = False

    if trend == "LONG":
        correction_count = down_closes
        correction_counter_label = "近8日下跌收盘数"
        recent_gap_to_value_band = (recent_close - recent_value_band_high).clip(lower=0)
        latest_gap_window = recent_gap_to_value_band.tail(DAILY_REVERSAL_LOOKBACK)
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
        higher_low_ref = float(low.tail(DAILY_CORRECTION_WINDOW_MAX).min())
        structure_break_level = higher_low_ref - (float(atr_series.iloc[-1]) * DAILY_STRUCTURE_BREACH_ATR_MULTIPLIER)
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
        custom_kline_confirmation = bool(
            custom_close_rule_pass and custom_wick_rule_pass and custom_close_location_rule_pass
        )
        accelerating_correction = bool(
            close.iloc[-1] < close.iloc[-2] < close.iloc[-3]
            and macd_hist_now < macd_hist_prev
        )
        rsi_strength = max(0.0, 45.0 - rsi_now)
        elder_core_checks = [
            value_zone_reached,
            histogram_reversal,
            structure_intact,
        ]
        setup_score = 1.3 + (1.0 if value_zone_reached else 0.0) + (1.5 if histogram_reversal else 0.0) + (1.0 if structure_intact else 0.0) + (0.2 if custom_kline_confirmation else 0.0)
        if not countertrend_exists:
            state = "REJECT"
            reject_reason = "周线做多但日线未形成可识别回调，不属于可执行 setup"
            rsi_state = "NO_PULLBACK"
        elif not structure_intact:
            state = "REJECT"
            reject_reason = "回调结构已明显跌穿防守摆点，止损边界不可定义"
            rsi_state = "STRUCTURE_BROKEN"
        elif accelerating_correction and not histogram_reversal:
            state = "REJECT"
            reject_reason = "日线回调仍在加速，尚未出现止跌减速迹象"
            rsi_state = "ACCELERATING_PULLBACK"
        elif not entered_value_zone:
            state = "WATCH"
            watch = True
            reject_reason = ""
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
        correction_counter_label = "近8日上涨收盘数"
        recent_gap_to_value_band = (recent_value_band_low - recent_close).clip(lower=0)
        latest_gap_window = recent_gap_to_value_band.tail(DAILY_REVERSAL_LOOKBACK)
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
        lower_high_ref = float(high.tail(DAILY_CORRECTION_WINDOW_MAX).max())
        structure_break_level = lower_high_ref + (float(atr_series.iloc[-1]) * DAILY_STRUCTURE_BREACH_ATR_MULTIPLIER)
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
        custom_kline_confirmation = bool(
            custom_close_rule_pass and custom_wick_rule_pass and custom_close_location_rule_pass
        )
        accelerating_correction = bool(
            close.iloc[-1] > close.iloc[-2] > close.iloc[-3]
            and macd_hist_now > macd_hist_prev
        )
        rsi_strength = max(0.0, rsi_now - 55.0)
        elder_core_checks = [
            value_zone_reached,
            histogram_reversal,
            structure_intact,
        ]
        setup_score = 1.3 + (1.0 if value_zone_reached else 0.0) + (1.5 if histogram_reversal else 0.0) + (1.0 if structure_intact else 0.0) + (0.2 if custom_kline_confirmation else 0.0)
        if not countertrend_exists:
            state = "REJECT"
            reject_reason = "周线做空但日线未形成可识别反弹，不属于可执行 setup"
            rsi_state = "NO_RALLY"
        elif not structure_intact:
            state = "REJECT"
            reject_reason = "反弹结构已明显突破防守摆点，止损边界不可定义"
            rsi_state = "STRUCTURE_BROKEN"
        elif accelerating_correction and not histogram_reversal:
            state = "REJECT"
            reject_reason = "日线反弹仍在加速，尚未出现滞涨转弱迹象"
            rsi_state = "ACCELERATING_RALLY"
        elif not entered_value_zone:
            state = "WATCH"
            watch = True
            reject_reason = ""
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
        countertrend_exists = False
        entered_value_zone = False
        value_zone_reached = False
        structure_intact = False
        elder_core_checks = [False, False, False]
        rsi_strength = 0.0
        setup_score = 0.0
        state = "REJECT"
        reject_reason = "周线方向不明，日线不单独提供交易资格"
        rsi_state = "NEUTRAL"
        histogram_reversal = False
        custom_kline_confirmation = False
        close_location_pct = 0.0
        wick_ratio_pct = 0.0
        close_above_prev = False
        close_below_prev = False
        latest_open = 0.0
        latest_close = 0.0
        latest_high = 0.0
        latest_low = 0.0
        macd_hist_now = 0.0
        macd_hist_prev = 0.0

    elder_core_signal_count = int(sum(elder_core_checks))
    if trend == "LONG":
        direction_label = "回调"
    elif trend == "SHORT":
        direction_label = "反弹"
    else:
        direction_label = "修正"

    value_zone_label = (
        f"{value_band_low.iloc[-1]:.2f}~{value_band_high.iloc[-1]:.2f}，最新区间 {latest_low:.2f}~{latest_high:.2f}"
        if trend in {"LONG", "SHORT"}
        else "—"
    )
    structure_label = (
        f"最新低点 {latest_low:.2f} >= 防守位 {structure_break_level:.2f}" if trend == "LONG" and structure_break_level is not None
        else f"最新高点 {latest_high:.2f} <= 防守位 {structure_break_level:.2f}" if trend == "SHORT" and structure_break_level is not None
        else "—"
    )
    histogram_label = f"Histogram {macd_hist_prev:+.4f}->{macd_hist_now:+.4f}" if trend in {"LONG", "SHORT"} else "—"
    detail_prefix = (
        f"{correction_counter_label} {correction_count}；"
        f"13EMA 价值带 {value_zone_label}；"
        f"{structure_label}；"
        f"Histogram 检查 {histogram_label}。"
    )

    if state == "REJECT":
        reason = f"{detail_prefix} 结论：{reject_reason}"
    elif state == "QUALIFIED":
        reason = (
            f"{detail_prefix} 结论：{direction_label} setup 已具备执行条件，"
            f"当前 {elder_core_signal_count}/3 项 Elder 核心信号到位，可进入候选池。"
        )
    else:
        reason = (
            f"{detail_prefix} 结论：{direction_label} setup 已出现，但当前仅 {elder_core_signal_count}/3 项 Elder 核心信号到位，继续观察。"
            if value_zone_reached
            else f"{detail_prefix} 结论：{direction_label} setup 已出现，但还没回到 13EMA 价值带，继续观察。"
        )

    return {
        "rsi": round(rsi_now, 2),
        "rsi_prev": round(rsi_prev, 2),
        "rsi_state": rsi_state,
        "rsi_strength": round(rsi_strength, 2),
        "setup_score": round(min(setup_score, DAILY_SETUP_SCORE_CAP), 2),
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
        "momentum_hist_now": round(float(macd_hist_now), 6) if trend in {"LONG", "SHORT"} else 0.0,
        "momentum_hist_prev": round(float(macd_hist_prev), 6) if trend in {"LONG", "SHORT"} else 0.0,
        "momentum_hist_delta": round(float(macd_hist_delta), 6) if trend in {"LONG", "SHORT"} else 0.0,
        "price_reversal": custom_kline_confirmation if trend in {"LONG", "SHORT"} else False,
        "custom_kline_confirmation": custom_kline_confirmation if trend in {"LONG", "SHORT"} else False,
        "custom_close_vs_prev": close_above_prev if trend == "LONG" else close_below_prev if trend == "SHORT" else False,
        "custom_close_rule_pass": custom_close_rule_pass if trend in {"LONG", "SHORT"} else False,
        "custom_wick_rule_pass": custom_wick_rule_pass if trend in {"LONG", "SHORT"} else False,
        "custom_close_location_rule_pass": custom_close_location_rule_pass if trend in {"LONG", "SHORT"} else False,
        "custom_wick_ratio_pct": round(float(wick_ratio_pct), 2) if trend in {"LONG", "SHORT"} else 0.0,
        "custom_close_location_pct": round(float(close_location_pct), 2) if trend in {"LONG", "SHORT"} else 0.0,
        "priority_divergence": False,
        "earnings_blocked": False,
        "watch": watch,
        "pass": passed,
        "reason": reason,
    }


def _split_hourly_execution_bars(
    df_hour: pd.DataFrame,
    as_of: datetime | None = None,
) -> tuple[pd.DataFrame, pd.Series | None, bool]:
    if df_hour.empty:
        return df_hour, None, False

    reference_time = pd.Timestamp(as_of or datetime.now(UTC))
    if reference_time.tzinfo is None:
        reference_time = reference_time.tz_localize("UTC")
    else:
        reference_time = reference_time.tz_convert("UTC")

    latest_open = pd.Timestamp(df_hour.index[-1])
    if latest_open.tzinfo is None:
        latest_open = latest_open.tz_localize("UTC")
    else:
        latest_open = latest_open.tz_convert("UTC")

    latest_is_live = latest_open <= reference_time < latest_open + pd.Timedelta(hours=1)
    if latest_is_live:
        return df_hour.iloc[:-1].copy(), df_hour.iloc[-1].copy(), True
    return df_hour.copy(), None, False


def screen_hourly(
    df_hour: pd.DataFrame | None,
    trend: str,
    settings: StrategyConfig,
    as_of: datetime | None = None,
) -> dict:
    if df_hour is None or df_hour.empty:
        return {"pass": False, "reason": "小时线数据不足"}

    closed_bars, live_bar, live_bar_available = _split_hourly_execution_bars(df_hour, as_of=as_of)
    minimum_closed = settings.hourly.atr_period + 1
    if len(closed_bars) < minimum_closed:
        return {"pass": False, "reason": "小时线已收盘数据不足"}

    signal_bar = closed_bars.iloc[-1]
    atr = float(calc_atr(closed_bars, settings.hourly.atr_period).iloc[-1])
    signal_bar_high = float(signal_bar["high"])
    signal_bar_low = float(signal_bar["low"])
    signal_bar_close = float(signal_bar["close"])

    if live_bar_available and live_bar is not None:
        close = float(live_bar["close"])
        current_high = float(live_bar["high"])
        current_low = float(live_bar["low"])
    else:
        close = signal_bar_close
        current_high = signal_bar_high
        current_low = signal_bar_low

    breakout_long = live_bar_available and current_high > signal_bar_high
    breakout_short = live_bar_available and current_low < signal_bar_low
    breakout_strength = 0.0
    passed = False
    trigger_price = signal_bar_high if trend == "LONG" else signal_bar_low if trend == "SHORT" else close
    trigger_gap = 0.0
    status = "NEUTRAL"
    trigger_score = 0.0

    if trend == "LONG" and breakout_long and atr > 0:
        passed = True
        breakout_strength = (current_high - signal_bar_high) / atr
        trigger_price = signal_bar_high
        trigger_gap = current_high - signal_bar_high
        status = "TRIGGERED"
        trigger_score = 2.2 + min(breakout_strength * 1.2, 1.8)
    elif trend == "LONG":
        trigger_gap = max(signal_bar_high - current_high, 0.0)
        status = "WAITING_BREAKOUT" if live_bar_available else "WAITING_NEXT_BAR"
        if atr > 0:
            trigger_score = max(0.0, 1.8 - min(trigger_gap / atr, 1.8))
    elif trend == "SHORT" and breakout_short and atr > 0:
        passed = True
        breakout_strength = (signal_bar_low - current_low) / atr
        trigger_price = signal_bar_low
        trigger_gap = signal_bar_low - current_low
        status = "TRIGGERED"
        trigger_score = 2.2 + min(breakout_strength * 1.2, 1.8)
    elif trend == "SHORT":
        trigger_gap = max(current_low - signal_bar_low, 0.0)
        status = "WAITING_BREAKDOWN" if live_bar_available else "WAITING_NEXT_BAR"
        if atr > 0:
            trigger_score = max(0.0, 1.8 - min(trigger_gap / atr, 1.8))

    gap_atr = (trigger_gap / atr) if atr > 0 else 0.0
    if trend == "LONG":
        reason = (
            f"当前小时线已向上突破上一根已收盘K线高点，trailing buy-stop 触发（强度 {breakout_strength:.2f} ATR）"
            if passed
            else (
                f"小时线尚未触发，当前 buy-stop 跟踪到上一根已收盘K线高点上方（距离 {trigger_gap:.2f}，约 {gap_atr:.2f} ATR）"
                if live_bar_available
                else "当前暂无进行中的小时K，下一根小时K开始后继续沿最近已收盘K线高点上移买入止损"
            )
        )
    elif trend == "SHORT":
        reason = (
            f"当前小时线已向下跌破上一根已收盘K线低点，trailing sell-stop 触发（强度 {breakout_strength:.2f} ATR）"
            if passed
            else (
                f"小时线尚未触发，当前 sell-stop 跟踪到上一根已收盘K线低点下方（距离 {trigger_gap:.2f}，约 {gap_atr:.2f} ATR）"
                if live_bar_available
                else "当前暂无进行中的小时K，下一根小时K开始后继续沿最近已收盘K线低点下移卖出止损"
            )
        )
    else:
        reason = "小时线未匹配周线方向"

    return {
        "close": round(close, 4),
        "current_high": round(current_high, 4),
        "current_low": round(current_low, 4),
        "high_n": round(signal_bar_high, 4),
        "low_n": round(signal_bar_low, 4),
        "signal_bar_high": round(signal_bar_high, 4),
        "signal_bar_low": round(signal_bar_low, 4),
        "atr": round(atr, 4),
        "status": status,
        "entry_price": round(trigger_price, 4),
        "trailing_stop_price": round(trigger_price, 4),
        "trigger_gap": round(trigger_gap, 4),
        "trigger_gap_atr": round(gap_atr, 3),
        "breakout_long": breakout_long,
        "breakout_short": breakout_short,
        "breakout_strength": round(breakout_strength, 3),
        "live_bar_available": live_bar_available,
        "trigger_score": round(min(trigger_score, HOURLY_TRIGGER_SCORE_CAP), 2),
        "pass": passed,
        "reason": reason,
    }


def calc_exits(
    direction: str,
    entry: float,
    daily_frame: pd.DataFrame | None,
    atr: float,
    trade_plan: TradePlanConfig,
    signal_bar_high: float | None = None,
    signal_bar_low: float | None = None,
) -> dict:
    if daily_frame is None or daily_frame.empty:
        stop_loss = entry
        initial_stop_loss = entry
        protective_stop_loss = entry
        take_profit = entry
        thermometer = 0.0
        thermometer_ema = 0.0
        safezone_stop = entry
        pullback_pivot_stop = entry
        signal_bar_stop = entry
        stop_basis = "UNKNOWN"
        initial_stop_basis = "UNKNOWN"
        protective_stop_basis = "UNKNOWN"
        target_reference = entry
        safezone_noise = 0.0
    else:
        latest_high = float(daily_frame["high"].iloc[-1])
        latest_low = float(daily_frame["low"].iloc[-1])
        safezone_stop, safezone_noise = calc_safezone_stop(daily_frame, direction, trade_plan)
        pullback_pivot_stop = calc_pullback_pivot_stop(daily_frame, direction)
        temperature, average_temperature = calc_market_thermometer(daily_frame, trade_plan.thermometer_period)
        thermometer = float(temperature.iloc[-1])
        thermometer_ema = float(average_temperature.iloc[-1])
        projected_move = thermometer_ema * trade_plan.thermometer_target_multiplier

        if direction == "LONG":
            signal_bar_stop = float(signal_bar_low) if signal_bar_low is not None else latest_low
            pullback_pivot_stop = pullback_pivot_stop if pullback_pivot_stop is not None else signal_bar_stop
            initial_stop_loss = min(signal_bar_stop, pullback_pivot_stop)
            initial_stop_basis = "PULLBACK_PIVOT" if pullback_pivot_stop < signal_bar_stop else "SIGNAL_BAR"
            protective_stop_loss = safezone_stop if safezone_stop is not None else initial_stop_loss
            protective_stop_basis = "SAFEZONE" if safezone_stop is not None else initial_stop_basis
            stop_loss = initial_stop_loss
            stop_basis = initial_stop_basis
            target_reference = max(entry, latest_high)
            take_profit = target_reference + projected_move
        else:
            signal_bar_stop = float(signal_bar_high) if signal_bar_high is not None else latest_high
            pullback_pivot_stop = pullback_pivot_stop if pullback_pivot_stop is not None else signal_bar_stop
            initial_stop_loss = max(signal_bar_stop, pullback_pivot_stop)
            initial_stop_basis = "PULLBACK_PIVOT" if pullback_pivot_stop > signal_bar_stop else "SIGNAL_BAR"
            protective_stop_loss = safezone_stop if safezone_stop is not None else initial_stop_loss
            protective_stop_basis = "SAFEZONE" if safezone_stop is not None else initial_stop_basis
            stop_loss = initial_stop_loss
            stop_basis = initial_stop_basis
            target_reference = min(entry, latest_low)
            take_profit = target_reference - projected_move

    risk_per_share = abs(entry - initial_stop_loss)
    reward_per_share = abs(take_profit - entry)
    reward_risk = (reward_per_share / risk_per_share) if risk_per_share > 0 else 0.0

    return {
        "entry": round(entry, 4),
        "stop_loss": round(stop_loss, 4),
        "initial_stop_loss": round(initial_stop_loss, 4),
        "initial_stop_signal_bar": round(signal_bar_stop, 4),
        "initial_stop_pullback_pivot": round(pullback_pivot_stop, 4),
        "initial_stop_basis": initial_stop_basis,
        "protective_stop_loss": round(protective_stop_loss, 4),
        "protective_stop_basis": protective_stop_basis,
        "stop_loss_safezone": round(safezone_stop, 4),
        "stop_loss_two_bar": round(pullback_pivot_stop, 4),
        "stop_basis": stop_basis,
        "take_profit": round(take_profit, 4),
        "target_reference": round(target_reference, 4),
        "thermometer": round(thermometer, 4),
        "thermometer_ema": round(thermometer_ema, 4),
        "safezone_noise": round(safezone_noise, 4),
        "risk_per_share": round(risk_per_share, 4),
        "reward_per_share": round(reward_per_share, 4),
        "reward_risk_ratio": round(reward_risk, 2),
        "atr": round(atr, 4),
        "exit_timeframe": "DAY",
    }


def calc_candidate_score(weekly_result: dict, daily_result: dict) -> float:
    score = 0.0

    score += min(weekly_result.get("trend_score", 0), WEEKLY_TREND_SCORE_CAP) / WEEKLY_TREND_SCORE_CAP * 4.6
    score += min(daily_result.get("setup_score", 0), DAILY_SETUP_SCORE_CAP) / DAILY_SETUP_SCORE_CAP * 4.5
    if weekly_result.get("pass"):
        score += 0.45
    if daily_result.get("pass"):
        score += 0.45

    return round(min(score, 10), 2)


def calc_execution_score(weekly_result: dict, daily_result: dict, hourly_result: dict, exits: dict) -> float:
    score = 0.0

    score += min(weekly_result.get("trend_score", 0), WEEKLY_TREND_SCORE_CAP) / WEEKLY_TREND_SCORE_CAP * 2.4
    score += min(daily_result.get("setup_score", 0), DAILY_SETUP_SCORE_CAP) / DAILY_SETUP_SCORE_CAP * 2.4
    score += min(hourly_result.get("trigger_score", 0), HOURLY_TRIGGER_SCORE_CAP) / HOURLY_TRIGGER_SCORE_CAP * 1.8
    score += calc_reward_risk_score(float(exits.get("reward_risk_ratio", 0.0)))

    if weekly_result.get("pass"):
        score += 0.4
    if daily_result.get("pass"):
        score += 0.4
    if hourly_result.get("pass"):
        score += 0.6

    return round(min(score, 10), 2)


def calc_signal_score(weekly_result: dict, daily_result: dict, hourly_result: dict, exits: dict) -> float:
    return calc_execution_score(weekly_result, daily_result, hourly_result, exits)
