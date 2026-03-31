from __future__ import annotations

import numpy as np
import pandas as pd

from triple_screen.config.schema import RiskConfig, StrategyConfig


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
        return {"trend": "NEUTRAL", "pass": False, "reason": "数据不足"}

    macd, signal, histogram = calc_macd(df_week, settings)
    hist_now = histogram.iloc[-1]
    hist_prev = histogram.iloc[-2]

    confirmed = 0
    for value in reversed(histogram.values):
        if (hist_now > 0 and value > 0) or (hist_now < 0 and value < 0):
            confirmed += 1
        else:
            break

    if hist_now > 0:
        trend = "LONG"
    elif hist_now < 0:
        trend = "SHORT"
    else:
        trend = "NEUTRAL"

    return {
        "trend": trend,
        "histogram": round(float(hist_now), 6),
        "histogram_prev": round(float(hist_prev), 6),
        "histogram_strength": abs(float(hist_now)),
        "histogram_growing": abs(float(hist_now)) > abs(float(hist_prev)),
        "macd": round(float(macd.iloc[-1]), 6),
        "macd_signal": round(float(signal.iloc[-1]), 6),
        "confirmed_bars": confirmed,
        "pass": trend != "NEUTRAL" and confirmed >= settings.weekly.confirm_bars,
        "reason": f"周线 MACD Histogram={hist_now:+.4f}",
    }


def screen_daily(df_day: pd.DataFrame | None, trend: str, settings: StrategyConfig) -> dict:
    if df_day is None or len(df_day) < settings.daily.rsi_period + 5:
        return {"pass": False, "reason": "数据不足"}

    rsi = calc_rsi(df_day, settings.daily.rsi_period)
    rsi_now = float(rsi.iloc[-1])
    rsi_prev = float(rsi.iloc[-2])

    passed = False
    rsi_state = "NEUTRAL"

    if trend == "LONG":
        if settings.daily.recovery_mode:
            if rsi_now < settings.daily.rsi_oversold:
                rsi_state = "OVERSOLD"
                passed = True
            elif rsi_prev < 30 and rsi_now >= 30:
                rsi_state = "RECOVERING"
                passed = True
        else:
            passed = rsi_now < settings.daily.rsi_oversold
            rsi_state = "OVERSOLD" if passed else "NEUTRAL"
        rsi_strength = max(0.0, settings.daily.rsi_oversold - rsi_now)
    elif trend == "SHORT":
        if settings.daily.recovery_mode:
            if rsi_now > settings.daily.rsi_overbought:
                rsi_state = "OVERBOUGHT"
                passed = True
            elif rsi_prev > 70 and rsi_now <= 70:
                rsi_state = "FALLING"
                passed = True
        else:
            passed = rsi_now > settings.daily.rsi_overbought
            rsi_state = "OVERBOUGHT" if passed else "NEUTRAL"
        rsi_strength = max(0.0, rsi_now - settings.daily.rsi_overbought)
    else:
        rsi_strength = 0.0

    return {
        "rsi": round(rsi_now, 2),
        "rsi_prev": round(rsi_prev, 2),
        "rsi_state": rsi_state,
        "rsi_strength": round(rsi_strength, 2),
        "pass": passed,
        "reason": f"日线 RSI={rsi_now:.1f} ({rsi_state})",
    }


def screen_hourly(df_hour: pd.DataFrame | None, trend: str, settings: StrategyConfig) -> dict:
    minimum = settings.hourly.breakout_bars + settings.hourly.atr_period + 2
    if df_hour is None or len(df_hour) < minimum:
        return {"pass": False, "reason": "数据不足"}

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

    if trend == "LONG" and breakout_long and atr > 0:
        passed = True
        breakout_strength = (close - high_n) / atr
    elif trend == "SHORT" and breakout_short and atr > 0:
        passed = True
        breakout_strength = (low_n - close) / atr

    return {
        "close": round(close, 4),
        "high_n": round(high_n, 4),
        "low_n": round(low_n, 4),
        "atr": round(atr, 4),
        "breakout_long": breakout_long,
        "breakout_short": breakout_short,
        "breakout_strength": round(breakout_strength, 3),
        "pass": passed,
        "reason": f"1H breakout={passed}",
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

    score += min(weekly_result.get("histogram_strength", 0) * 10, 3)
    if weekly_result.get("histogram_growing"):
        score += 0.5

    score += min(daily_result.get("rsi_strength", 0) / 5, 3)
    score += min(hourly_result.get("breakout_strength", 0) * 4, 4)

    if weekly_result.get("confirmed_bars", 0) >= 3:
        score += 0.5

    return round(min(score, 10), 2)
