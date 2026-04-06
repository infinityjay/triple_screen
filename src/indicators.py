from __future__ import annotations

import numpy as np
import pandas as pd

from schema import RiskConfig, StrategyConfig

RSI_WATCH_BUFFER = 5.0


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
        reason = f"周线偏多，MACD柱线抬升 {hist_prev:+.4f} -> {hist_now:+.4f}"
    elif trend == "SHORT":
        setup_state = "BEARISH_SLOPE"
        reason = f"周线偏空，MACD柱线回落 {hist_prev:+.4f} -> {hist_now:+.4f}"
    else:
        setup_state = "NEUTRAL"
        reason = "周线无明确方向"

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
        "trend_score": round(min(trend_score, 4.5), 2),
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

    passed = False
    watch = False
    rsi_state = "NEUTRAL"
    setup_score = 0.0

    if trend == "LONG":
        if rsi_now <= settings.daily.rsi_oversold:
            rsi_state = "OVERSOLD"
            passed = True
        elif settings.daily.recovery_mode and rsi_prev <= settings.daily.rsi_oversold and rsi_now > rsi_prev:
            rsi_state = "RECOVERING"
            passed = True
        elif rsi_now <= settings.daily.rsi_oversold + RSI_WATCH_BUFFER:
            rsi_state = "PULLBACK_WATCH"
            watch = True

        rsi_strength = max(0.0, settings.daily.rsi_oversold - rsi_now)
        if passed:
            setup_score = 2.2 + min(rsi_strength / 4, 1.6)
            if rsi_state == "RECOVERING":
                setup_score += 0.6
        elif watch:
            setup_score = 1.2 + min((settings.daily.rsi_oversold + RSI_WATCH_BUFFER - rsi_now) / 5, 0.8)
    elif trend == "SHORT":
        if rsi_now >= settings.daily.rsi_overbought:
            rsi_state = "OVERBOUGHT"
            passed = True
        elif settings.daily.recovery_mode and rsi_prev >= settings.daily.rsi_overbought and rsi_now < rsi_prev:
            rsi_state = "ROLLING_OVER"
            passed = True
        elif rsi_now >= settings.daily.rsi_overbought - RSI_WATCH_BUFFER:
            rsi_state = "RALLY_WATCH"
            watch = True

        rsi_strength = max(0.0, rsi_now - settings.daily.rsi_overbought)
        if passed:
            setup_score = 2.2 + min(rsi_strength / 4, 1.6)
            if rsi_state == "ROLLING_OVER":
                setup_score += 0.6
        elif watch:
            setup_score = 1.2 + min((rsi_now - (settings.daily.rsi_overbought - RSI_WATCH_BUFFER)) / 5, 0.8)
    else:
        rsi_strength = 0.0
        setup_score = 0.0

    if trend == "LONG":
        reason = f"日线 RSI={rsi_now:.1f}，状态 {rsi_state}"
    elif trend == "SHORT":
        reason = f"日线 RSI={rsi_now:.1f}，状态 {rsi_state}"
    else:
        reason = f"日线 RSI={rsi_now:.1f}，但周线无方向"

    return {
        "rsi": round(rsi_now, 2),
        "rsi_prev": round(rsi_prev, 2),
        "rsi_state": rsi_state,
        "rsi_strength": round(rsi_strength, 2),
        "setup_score": round(min(setup_score, 4.5), 2),
        "watch": watch,
        "pass": passed,
        "reason": reason,
    }


def screen_hourly(df_hour: pd.DataFrame | None, trend: str, settings: StrategyConfig) -> dict:
    minimum = settings.hourly.breakout_bars + settings.hourly.atr_period + 2
    if df_hour is None or len(df_hour) < minimum:
        return {"pass": False, "reason": "小时线数据不足"}

    close = float(df_hour["close"].iloc[-1])
    atr = float(calc_atr(df_hour, settings.hourly.atr_period).iloc[-1])

    prev_highs = df_hour["high"].iloc[-(settings.hourly.breakout_bars + 1) : -1]
    prev_lows = df_hour["low"].iloc[-(settings.hourly.breakout_bars + 1) : -1]
    high_n = float(prev_highs.max())
    low_n = float(prev_lows.min())

    breakout_long = close > high_n
    breakout_short = close < low_n
    breakout_strength = 0.0
    passed = False
    trigger_price = close
    trigger_gap = 0.0
    status = "NEUTRAL"
    trigger_score = 0.0

    if trend == "LONG" and breakout_long and atr > 0:
        passed = True
        breakout_strength = (close - high_n) / atr
        trigger_price = close
        trigger_gap = close - high_n
        status = "TRIGGERED"
        trigger_score = 2.2 + min(breakout_strength * 1.2, 1.8)
    elif trend == "LONG":
        trigger_price = high_n
        trigger_gap = high_n - close
        status = "WAITING_BREAKOUT"
        if atr > 0:
            trigger_score = max(0.0, 1.8 - min(trigger_gap / atr, 1.8))
    elif trend == "SHORT" and breakout_short and atr > 0:
        passed = True
        breakout_strength = (low_n - close) / atr
        trigger_price = close
        trigger_gap = low_n - close
        status = "TRIGGERED"
        trigger_score = 2.2 + min(breakout_strength * 1.2, 1.8)
    elif trend == "SHORT":
        trigger_price = low_n
        trigger_gap = close - low_n
        status = "WAITING_BREAKDOWN"
        if atr > 0:
            trigger_score = max(0.0, 1.8 - min(trigger_gap / atr, 1.8))

    gap_atr = (trigger_gap / atr) if atr > 0 else 0.0
    if trend == "LONG":
        reason = (
            f"小时线已触发向上突破，强度 {breakout_strength:.2f} ATR"
            if passed
            else f"小时线待突破，距离触发价 {trigger_gap:.2f} ({gap_atr:.2f} ATR)"
        )
    elif trend == "SHORT":
        reason = (
            f"小时线已触发向下跌破，强度 {breakout_strength:.2f} ATR"
            if passed
            else f"小时线待跌破，距离触发价 {trigger_gap:.2f} ({gap_atr:.2f} ATR)"
        )
    else:
        reason = "小时线未匹配周线方向"

    return {
        "close": round(close, 4),
        "high_n": round(high_n, 4),
        "low_n": round(low_n, 4),
        "atr": round(atr, 4),
        "status": status,
        "entry_price": round(trigger_price, 4),
        "trigger_gap": round(trigger_gap, 4),
        "trigger_gap_atr": round(gap_atr, 3),
        "breakout_long": breakout_long,
        "breakout_short": breakout_short,
        "breakout_strength": round(breakout_strength, 3),
        "trigger_score": round(min(trigger_score, 4.0), 2),
        "pass": passed,
        "reason": reason,
    }


def calc_exits(
    direction: str,
    entry: float,
    atr: float,
    risk: RiskConfig,
    prev_candle_low: float | None = None,
    prev_candle_high: float | None = None,
) -> dict:
    sl_distance = atr * risk.atr_multiplier
    tp_distance = sl_distance * risk.reward_risk_ratio

    if direction == "LONG":
        sl_atr = entry - sl_distance
        sl_prev = prev_candle_low if prev_candle_low is not None else sl_atr
        stop_loss = min(sl_atr, sl_prev)
        take_profit = entry + tp_distance
    else:
        sl_atr = entry + sl_distance
        sl_prev = prev_candle_high if prev_candle_high is not None else sl_atr
        stop_loss = max(sl_atr, sl_prev)
        take_profit = entry - tp_distance

    risk_per_share = abs(entry - stop_loss)
    position_size = (risk.account_size * risk.account_risk_pct) / risk_per_share if risk_per_share > 0 else 0.0

    return {
        "entry": round(entry, 4),
        "sl_atr": round(sl_atr, 4),
        "sl_prev_candle": round(sl_prev, 4),
        "stop_loss_final": round(stop_loss, 4),
        "tp_fixed_rr": round(take_profit, 4),
        "risk_per_share": round(risk_per_share, 4),
        "position_size": round(position_size, 2),
        "atr": round(atr, 4),
    }


def calc_signal_score(weekly_result: dict, daily_result: dict, hourly_result: dict) -> float:
    score = 0.0

    score += weekly_result.get("trend_score", 0) * 0.8
    score += daily_result.get("setup_score", 0) * 0.9
    score += hourly_result.get("trigger_score", 0) * 0.7

    if weekly_result.get("pass"):
        score += 0.2
    if daily_result.get("pass"):
        score += 0.3
    if hourly_result.get("pass"):
        score += 0.5

    return round(min(score, 10), 2)
