# src/indicators.py
# 三重过滤指标计算核心

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────
# 基础指标函数
# ─────────────────────────────────────────────

def calc_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def calc_macd(close: pd.Series, fast=12, slow=26, signal=9):
    """返回 (macd_line, signal_line, histogram)"""
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_stochastic(df: pd.DataFrame, k_period=14, d_period=3):
    """返回 (K, D)，均为 0-100"""
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    denom = high_max - low_min
    k = 100 * (df["close"] - low_min) / denom.replace(0, np.nan)
    d = k.rolling(d_period).mean()
    return k, d


# ─────────────────────────────────────────────
# 三重过滤主函数
# ─────────────────────────────────────────────

def run_triple_screen(daily_df: pd.DataFrame,
                      weekly_df: pd.DataFrame,
                      settings: dict = None) -> dict:
    """
    三重过滤系统（Elder《以交易为生》）

    第一重：周线趋势方向（EMA13 + MACD histogram）
    第二重：日线动量（Stochastic K/D）
    第三重：日线价格相对 EMA22 位置

    返回包含信号和所有指标值的字典
    """
    if settings is None:
        settings = {}

    # 读取参数（有默认值）
    weekly_ema_p = settings.get("weekly_ema_period", 13)
    macd_fast    = settings.get("macd_fast", 12)
    macd_slow    = settings.get("macd_slow", 26)
    macd_sig     = settings.get("macd_signal", 9)
    stoch_k      = settings.get("stoch_k", 14)
    stoch_d      = settings.get("stoch_d", 3)
    daily_ema_p  = settings.get("daily_ema_period", 22)

    # 数据量检查
    if len(weekly_df) < weekly_ema_p + 10 or len(daily_df) < macd_slow + stoch_k + 10:
        return {
            "signal": "INSUFFICIENT_DATA",
            "error": "历史数据不足，请先运行初始化"
        }

    # ── 第一重：周线趋势 ──────────────────────────
    weekly_ema = calc_ema(weekly_df["close"], weekly_ema_p)
    _, _, weekly_hist = calc_macd(weekly_df["close"], macd_fast, macd_slow, macd_sig)

    last_weekly_close = weekly_df["close"].iloc[-1]
    weekly_ema_val    = weekly_ema.iloc[-1]
    trend_up          = bool(last_weekly_close > weekly_ema_val)
    # MACD histogram 连续上升 = 牛市动能增强
    macd_bullish      = bool(weekly_hist.iloc[-1] > weekly_hist.iloc[-2])
    macd_hist_val     = float(weekly_hist.iloc[-1])

    # ── 第二重：日线 Stochastic ───────────────────
    k, d = calc_stochastic(daily_df, stoch_k, stoch_d)
    k_val = float(k.iloc[-1])
    d_val = float(d.iloc[-1])
    k_prev = float(k.iloc[-2])
    d_prev = float(d.iloc[-2])

    stoch_oversold   = k_val < 30 and d_val < 30
    stoch_overbought = k_val > 70 and d_val > 70
    stoch_cross_up   = k_val > d_val and k_prev <= d_prev   # K上穿D
    stoch_cross_down = k_val < d_val and k_prev >= d_prev   # K下穿D

    # ── 第三重：日线价格位置 ──────────────────────
    daily_ema = calc_ema(daily_df["close"], daily_ema_p)
    last_close       = float(daily_df["close"].iloc[-1])
    daily_ema_val    = float(daily_ema.iloc[-1])
    price_above_ema  = bool(last_close > daily_ema_val)

    # ── 综合信号判断 ──────────────────────────────
    if trend_up and macd_bullish and stoch_oversold and stoch_cross_up:
        signal = "BUY"           # 三重共振：趋势上行 + 动量回调后反转
    elif not trend_up and not macd_bullish and stoch_overbought and stoch_cross_down:
        signal = "SELL"          # 三重共振：趋势下行 + 动量超买后反转
    elif trend_up and macd_bullish and not stoch_overbought:
        signal = "WATCH_LONG"    # 趋势向上，等待回调入场机会
    elif not trend_up and not macd_bullish and not stoch_oversold:
        signal = "WATCH_SHORT"   # 趋势向下，等待反弹做空机会
    elif trend_up and not macd_bullish:
        signal = "CAUTION_LONG"  # 价格在EMA上方但MACD减弱，谨慎
    else:
        signal = "NEUTRAL"

    return {
        "signal":             signal,
        # 第一重
        "weekly_trend":       "UP" if trend_up else "DOWN",
        "weekly_ema13":       round(float(weekly_ema_val), 2),
        "weekly_close":       round(float(last_weekly_close), 2),
        "weekly_macd_bullish": macd_bullish,
        "weekly_macd_hist":   round(macd_hist_val, 4),
        # 第二重
        "daily_stoch_k":      round(k_val, 1),
        "daily_stoch_d":      round(d_val, 1),
        "stoch_cross_up":     stoch_cross_up,
        "stoch_cross_down":   stoch_cross_down,
        # 第三重
        "last_close":         round(last_close, 2),
        "daily_ema22":        round(daily_ema_val, 2),
        "price_above_ema22":  price_above_ema,
    }
