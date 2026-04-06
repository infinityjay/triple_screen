from __future__ import annotations

import numpy as np
import pandas as pd

from schema import StrategyConfig, TradePlanConfig

RSI_WATCH_BUFFER = 5.0
DAILY_REVERSAL_LOOKBACK = 3
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


def screen_weekly(df_week: pd.DataFrame | None, settings: StrategyConfig) -> dict:
    required = settings.weekly.macd_slow + settings.weekly.macd_signal + 5
    if df_week is None or len(df_week) < required:
        return {"trend": "NEUTRAL", "pass": False, "actionable": False, "reason": "周线数据不足"}

    macd, signal, histogram = calc_macd(df_week, settings)
    hist_now = histogram.iloc[-1]
    hist_prev = histogram.iloc[-2]
    hist_delta = hist_now - hist_prev
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
    trend_score = 0.0
    if actionable:
        trend_score += min(abs(float(hist_delta)) * 40, 2.5)
        trend_score += min(confirmed, 4) * 0.35
        if (trend == "LONG" and hist_now < 0) or (trend == "SHORT" and hist_now > 0):
            trend_score += 0.8

    if trend == "LONG":
        setup_state = "BULLISH_SLOPE"
        reason = f"周线动能回升，多头占优，可继续观察回调后的做多机会（柱线 {hist_prev:+.4f} -> {hist_now:+.4f}）"
    elif trend == "SHORT":
        setup_state = "BEARISH_SLOPE"
        reason = f"周线动能回落，空头占优，可继续观察反弹后的做空机会（柱线 {hist_prev:+.4f} -> {hist_now:+.4f}）"
    else:
        setup_state = "NEUTRAL"
        reason = "周线动能方向不清晰，暂不作为重点交易对象"

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
        "confirmed_bars": confirmed,
        "trend_score": round(min(trend_score, WEEKLY_TREND_SCORE_CAP), 2),
        "actionable": actionable,
        "pass": actionable and confirmed_pass,
        "reason": reason,
    }


def screen_daily(df_day: pd.DataFrame | None, trend: str, settings: StrategyConfig) -> dict:
    if df_day is None or len(df_day) < settings.daily.rsi_period + 5:
        return {"pass": False, "watch": False, "reason": "日线数据不足"}

    rsi = calc_rsi(df_day, settings.daily.rsi_period)
    rsi_now = float(rsi.iloc[-1])
    rsi_prev = float(rsi.iloc[-2])
    recent_rsi = rsi.tail(DAILY_REVERSAL_LOOKBACK)

    passed = False
    watch = False
    rsi_state = "NEUTRAL"
    setup_score = 0.0

    if trend == "LONG":
        recent_oversold = bool((recent_rsi <= settings.daily.rsi_oversold).any())
        turning_up = rsi_now > rsi_prev

        if settings.daily.recovery_mode and recent_oversold and turning_up:
            rsi_state = "RECOVERING"
            passed = True
        elif not settings.daily.recovery_mode and rsi_now <= settings.daily.rsi_oversold:
            rsi_state = "OVERSOLD"
            passed = True
        elif rsi_now <= settings.daily.rsi_oversold:
            rsi_state = "OVERSOLD_WAIT"
            watch = True
        elif recent_oversold:
            rsi_state = "POST_OVERSOLD_WATCH"
            watch = True
        elif rsi_now <= settings.daily.rsi_oversold + RSI_WATCH_BUFFER:
            rsi_state = "PULLBACK_WATCH"
            watch = True

        rsi_strength = max(0.0, settings.daily.rsi_oversold - rsi_now)
        if passed:
            setup_score = 2.6 + min(max(settings.daily.rsi_oversold - min(float(recent_rsi.min()), rsi_now), 0.0) / 4, 1.4)
            if rsi_now <= settings.daily.rsi_oversold:
                setup_score += 0.2
        elif watch:
            setup_score = 1.2 + min((settings.daily.rsi_oversold + RSI_WATCH_BUFFER - rsi_now) / 5, 0.8)
    elif trend == "SHORT":
        recent_overbought = bool((recent_rsi >= settings.daily.rsi_overbought).any())
        turning_down = rsi_now < rsi_prev

        if settings.daily.recovery_mode and recent_overbought and turning_down:
            rsi_state = "ROLLING_OVER"
            passed = True
        elif not settings.daily.recovery_mode and rsi_now >= settings.daily.rsi_overbought:
            rsi_state = "OVERBOUGHT"
            passed = True
        elif rsi_now >= settings.daily.rsi_overbought:
            rsi_state = "OVERBOUGHT_WAIT"
            watch = True
        elif recent_overbought:
            rsi_state = "POST_OVERBOUGHT_WATCH"
            watch = True
        elif rsi_now >= settings.daily.rsi_overbought - RSI_WATCH_BUFFER:
            rsi_state = "RALLY_WATCH"
            watch = True

        rsi_strength = max(0.0, rsi_now - settings.daily.rsi_overbought)
        if passed:
            setup_score = 2.6 + min(max(max(float(recent_rsi.max()), rsi_now) - settings.daily.rsi_overbought, 0.0) / 4, 1.4)
            if rsi_now >= settings.daily.rsi_overbought:
                setup_score += 0.2
        elif watch:
            setup_score = 1.2 + min((rsi_now - (settings.daily.rsi_overbought - RSI_WATCH_BUFFER)) / 5, 0.8)
    else:
        rsi_strength = 0.0
        setup_score = 0.0

    if trend == "LONG":
        if rsi_state == "RECOVERING":
            reason = f"日线超卖后开始回升，回调可能接近完成，可等待小时线确认（RSI {rsi_prev:.1f} -> {rsi_now:.1f}）"
        elif rsi_state == "OVERSOLD":
            reason = f"日线已经进入超卖区，具备逆转基础，但仍需等待拐头确认（RSI {rsi_now:.1f}）"
        elif rsi_state == "OVERSOLD_WAIT":
            reason = f"日线仍在超卖区内下探，先等抛压缓和再看多头接回（RSI {rsi_now:.1f}）"
        elif rsi_state == "POST_OVERSOLD_WATCH":
            reason = f"日线刚从超卖区边缘抬头，多头修复在启动，仍需继续观察（RSI {rsi_prev:.1f} -> {rsi_now:.1f}）"
        elif rsi_state == "PULLBACK_WATCH":
            reason = f"日线处于回调观察区，距离理想低吸区不远，等待更明确的回升信号（RSI {rsi_now:.1f}）"
        else:
            reason = f"日线尚未形成理想的多头回调结构（RSI {rsi_now:.1f}）"
    elif trend == "SHORT":
        if rsi_state == "ROLLING_OVER":
            reason = f"日线超买后开始回落，反弹可能接近结束，可等待小时线确认（RSI {rsi_prev:.1f} -> {rsi_now:.1f}）"
        elif rsi_state == "OVERBOUGHT":
            reason = f"日线已经进入超买区，具备转弱基础，但仍需等待拐头确认（RSI {rsi_now:.1f}）"
        elif rsi_state == "OVERBOUGHT_WAIT":
            reason = f"日线仍在超买区内上冲，先等买盘降温再看空头接管（RSI {rsi_now:.1f}）"
        elif rsi_state == "POST_OVERBOUGHT_WATCH":
            reason = f"日线刚从超买区边缘回落，空头修复在启动，仍需继续观察（RSI {rsi_prev:.1f} -> {rsi_now:.1f}）"
        elif rsi_state == "RALLY_WATCH":
            reason = f"日线处于反弹观察区，距离理想高空区不远，等待更明确的回落信号（RSI {rsi_now:.1f}）"
        else:
            reason = f"日线尚未形成理想的空头反弹结构（RSI {rsi_now:.1f}）"
    else:
        reason = f"周线方向不明，日线信号暂不单独作为交易依据（RSI {rsi_now:.1f}）"

    return {
        "rsi": round(rsi_now, 2),
        "rsi_prev": round(rsi_prev, 2),
        "rsi_state": rsi_state,
        "rsi_strength": round(rsi_strength, 2),
        "setup_score": round(min(setup_score, DAILY_SETUP_SCORE_CAP), 2),
        "watch": watch,
        "pass": passed,
        "reason": reason,
    }


def screen_hourly(df_hour: pd.DataFrame | None, trend: str, settings: StrategyConfig) -> dict:
    minimum = settings.hourly.atr_period + 2
    if df_hour is None or len(df_hour) < minimum:
        return {"pass": False, "reason": "小时线数据不足"}

    close = float(df_hour["close"].iloc[-1])
    current_high = float(df_hour["high"].iloc[-1])
    current_low = float(df_hour["low"].iloc[-1])
    atr = float(calc_atr(df_hour, settings.hourly.atr_period).iloc[-1])
    signal_bar_high = float(df_hour["high"].iloc[-2])
    signal_bar_low = float(df_hour["low"].iloc[-2])

    breakout_long = current_high > signal_bar_high
    breakout_short = current_low < signal_bar_low
    breakout_strength = 0.0
    passed = False
    trigger_price = close
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
        trigger_price = current_high
        trigger_gap = current_high - close
        status = "WAITING_BREAKOUT"
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
        trigger_price = current_low
        trigger_gap = close - current_low
        status = "WAITING_BREAKDOWN"
        if atr > 0:
            trigger_score = max(0.0, 1.8 - min(trigger_gap / atr, 1.8))

    gap_atr = (trigger_gap / atr) if atr > 0 else 0.0
    if trend == "LONG":
        reason = (
            f"小时线已向上突破上一根K线高点，trailing buy-stop 触发（强度 {breakout_strength:.2f} ATR）"
            if passed
            else f"小时线尚未触发，下一笔可关注上一根K线高点上方的买入止损（距离 {trigger_gap:.2f}，约 {gap_atr:.2f} ATR）"
        )
    elif trend == "SHORT":
        reason = (
            f"小时线已向下跌破上一根K线低点，trailing sell-stop 触发（强度 {breakout_strength:.2f} ATR）"
            if passed
            else f"小时线尚未触发，下一笔可关注上一根K线低点下方的卖出止损（距离 {trigger_gap:.2f}，约 {gap_atr:.2f} ATR）"
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
        "trigger_gap": round(trigger_gap, 4),
        "trigger_gap_atr": round(gap_atr, 3),
        "breakout_long": breakout_long,
        "breakout_short": breakout_short,
        "breakout_strength": round(breakout_strength, 3),
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
) -> dict:
    if daily_frame is None or daily_frame.empty:
        stop_loss = entry
        take_profit = entry
        thermometer = 0.0
        thermometer_ema = 0.0
        safezone_stop = entry
        two_bar_stop = entry
        stop_basis = "UNKNOWN"
        target_reference = entry
        safezone_noise = 0.0
    else:
        latest_high = float(daily_frame["high"].iloc[-1])
        latest_low = float(daily_frame["low"].iloc[-1])
        if len(daily_frame) >= 2:
            two_bar_stop_long = min(float(daily_frame["low"].iloc[-2]), latest_low)
            two_bar_stop_short = max(float(daily_frame["high"].iloc[-2]), latest_high)
        else:
            two_bar_stop_long = latest_low
            two_bar_stop_short = latest_high

        safezone_stop, safezone_noise = calc_safezone_stop(daily_frame, direction, trade_plan)
        temperature, average_temperature = calc_market_thermometer(daily_frame, trade_plan.thermometer_period)
        thermometer = float(temperature.iloc[-1])
        thermometer_ema = float(average_temperature.iloc[-1])
        projected_move = thermometer_ema * trade_plan.thermometer_target_multiplier

        if direction == "LONG":
            two_bar_stop = two_bar_stop_long
            safezone_stop = safezone_stop if safezone_stop is not None else two_bar_stop
            stop_loss = min(two_bar_stop, safezone_stop)
            stop_basis = "SAFEZONE" if safezone_stop < two_bar_stop else "TWO_BAR"
            target_reference = max(entry, latest_high)
            take_profit = target_reference + projected_move
        else:
            two_bar_stop = two_bar_stop_short
            safezone_stop = safezone_stop if safezone_stop is not None else two_bar_stop
            stop_loss = max(two_bar_stop, safezone_stop)
            stop_basis = "SAFEZONE" if safezone_stop > two_bar_stop else "TWO_BAR"
            target_reference = min(entry, latest_low)
            take_profit = target_reference - projected_move

    risk_per_share = abs(entry - stop_loss)
    reward_per_share = abs(take_profit - entry)
    reward_risk = (reward_per_share / risk_per_share) if risk_per_share > 0 else 0.0

    return {
        "entry": round(entry, 4),
        "stop_loss": round(stop_loss, 4),
        "stop_loss_safezone": round(safezone_stop, 4),
        "stop_loss_two_bar": round(two_bar_stop, 4),
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


def calc_signal_score(weekly_result: dict, daily_result: dict, hourly_result: dict, exits: dict) -> float:
    score = 0.0

    score += min(weekly_result.get("trend_score", 0), WEEKLY_TREND_SCORE_CAP) / WEEKLY_TREND_SCORE_CAP * 2.4
    score += min(daily_result.get("setup_score", 0), DAILY_SETUP_SCORE_CAP) / DAILY_SETUP_SCORE_CAP * 2.5
    score += min(hourly_result.get("trigger_score", 0), HOURLY_TRIGGER_SCORE_CAP) / HOURLY_TRIGGER_SCORE_CAP * 1.6
    score += calc_reward_risk_score(float(exits.get("reward_risk_ratio", 0.0)))

    if weekly_result.get("pass"):
        score += 0.4
    if daily_result.get("pass"):
        score += 0.5
    if hourly_result.get("pass"):
        score += 0.6

    return round(min(score, 10), 2)
