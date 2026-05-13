from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from config.schema import StrategyConfig, TradePlanConfig

DAILY_REVERSAL_LOOKBACK = 3
DAILY_CORRECTION_WINDOW_MIN = 2
DAILY_CORRECTION_WINDOW_MAX = 8
DAILY_EMA_PERIOD = 13
ENTRY_PENETRATION_LOOKBACK = 10
FORCE_INDEX_EMA_PERIOD = 2
FORCE_INDEX_NEW_EXTREME_LOOKBACK = 15
DAILY_STRUCTURE_BREACH_ATR_MULTIPLIER = 0.3
CHANDELIER_LOOKBACK = 22
CHANDELIER_ATR_MULTIPLIER = 3.0
PARABOLIC_STEP = 0.02
PARABOLIC_MAX_STEP = 0.2
NICK_STOP_OFFSET = 0.01
NICK_STOP_LOOKBACK_DAYS = 20
MIN_TICK = 0.01
WEEKLY_VALUE_FAST_EMA = 13
WEEKLY_VALUE_SLOW_EMA = 26
WEEKLY_TREND_SCORE_CAP = 4.5
DAILY_SETUP_SCORE_CAP = 4.5
HOURLY_TRIGGER_SCORE_CAP = 4.0
REWARD_RISK_SCORE_CAP = 2.0
DIVERGENCE_LOOKBACK = 40
PIVOT_ORDER = 2


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _safezone_coefficient(plan: TradePlanConfig, direction: str) -> float:
    return plan.safezone_short_coefficient if direction == "SHORT" else plan.safezone_long_coefficient


def calc_macd(df: pd.DataFrame, settings: StrategyConfig) -> tuple[pd.Series, pd.Series, pd.Series]:
    close = df["close"]
    macd = calc_ema(close, settings.weekly.macd_fast) - calc_ema(close, settings.weekly.macd_slow)
    signal = calc_ema(macd, settings.weekly.macd_signal)
    histogram = macd - signal
    return macd, signal, histogram


def calc_force_index_ema(df: pd.DataFrame, period: int = FORCE_INDEX_EMA_PERIOD) -> pd.Series:
    close = df["close"].astype(float)
    volume = df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)
    force = close.diff() * volume
    return calc_ema(force.fillna(0.0), period)


def calc_impulse_system(df: pd.DataFrame, settings: StrategyConfig, ema_period: int = DAILY_EMA_PERIOD) -> dict:
    if df is None or len(df) < max(settings.weekly.macd_slow + settings.weekly.macd_signal + 2, ema_period + 2):
        return {
            "color": "BLUE",
            "direction": "NEUTRAL",
            "ema": None,
            "ema_prev": None,
            "ema_slope": 0.0,
            "macd": None,
            "macd_prev": None,
            "macd_slope": 0.0,
            "histogram": None,
            "histogram_prev": None,
            "histogram_delta": 0.0,
        }

    macd, signal, histogram = calc_macd(df, settings)
    ema = calc_ema(df["close"].astype(float), ema_period)
    ema_now = float(ema.iloc[-1])
    ema_prev = float(ema.iloc[-2])
    macd_now = float(macd.iloc[-1])
    macd_prev = float(macd.iloc[-2])
    hist_now = float(histogram.iloc[-1])
    hist_prev = float(histogram.iloc[-2])
    ema_slope = ema_now - ema_prev
    macd_slope = macd_now - macd_prev
    hist_delta = hist_now - hist_prev

    if ema_slope > 0 and macd_slope > 0:
        color = "GREEN"
        direction = "LONG"
    elif ema_slope < 0 and macd_slope < 0:
        color = "RED"
        direction = "SHORT"
    else:
        color = "BLUE"
        direction = "NEUTRAL"

    return {
        "color": color,
        "direction": direction,
        "ema": round(ema_now, 6),
        "ema_prev": round(ema_prev, 6),
        "ema_slope": round(ema_slope, 6),
        "macd": round(macd_now, 6),
        "macd_prev": round(macd_prev, 6),
        "macd_slope": round(macd_slope, 6),
        "macd_signal": round(float(signal.iloc[-1]), 6),
        "histogram": round(hist_now, 6),
        "histogram_prev": round(hist_prev, 6),
        "histogram_delta": round(hist_delta, 6),
    }


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
            "reason": f"{timeframe} data is insufficient for divergence detection",
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
            "reason": f"{timeframe} has no sufficiently clear pivot divergence",
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
            "reason": f"{timeframe} price pivots lack nearby MACD histogram pivots",
        }

    if direction == "SHORT":
        price_condition = high.iloc[second_price_pivot] > high.iloc[first_price_pivot]
        histogram_condition = histogram.iloc[second_hist_pivot] < histogram.iloc[first_hist_pivot]
        crossed_zero = bool((histogram.iloc[first_hist_pivot:second_hist_pivot] < 0).any())
        label = "bearish top divergence"
    else:
        price_condition = low.iloc[second_price_pivot] < low.iloc[first_price_pivot]
        histogram_condition = histogram.iloc[second_hist_pivot] > histogram.iloc[first_hist_pivot]
        crossed_zero = bool((histogram.iloc[first_hist_pivot:second_hist_pivot] > 0).any())
        label = "bullish bottom divergence"

    detected = bool(price_condition and histogram_condition and crossed_zero)
    if not detected:
        return {
            "detected": False,
            "strong_alert": False,
            "timeframe": timeframe,
            "direction": direction,
            "reason": f"{timeframe} recent pivots do not satisfy {label} conditions",
        }

    strong_alert = False
    exhaustion_reason = "No significant three-bar exhaustion pattern"
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
                    f"Middle bar in the latest three reached {middle_bar / baseline:.2f}x the median range of the prior 20 bars"
                )

    return {
        "detected": True,
        "strong_alert": strong_alert,
        "timeframe": timeframe,
        "direction": direction,
        "label": label,
        "first_price_pivot_at": str(frame.index[first_price_pivot]),
        "second_price_pivot_at": str(frame.index[second_price_pivot]),
        "reason": f"{timeframe}{label} is valid, with histogram crossing zero between pivots",
        "exhaustion_reason": exhaustion_reason,
    }


def calc_safezone_stop(df: pd.DataFrame, direction: str, plan: TradePlanConfig) -> tuple[float | None, float]:
    if df is None or len(df) < 2:
        return None, 0.0

    direction = str(direction).upper()
    if direction not in {"LONG", "SHORT"}:
        return None, 0.0

    lookback = max(plan.safezone_lookback, 1)
    ema_period = max(plan.safezone_ema_period, 1)
    ema_series = calc_ema(df["close"].astype(float), ema_period)
    reference_price = float(ema_series.iloc[-1])
    coefficient = _safezone_coefficient(plan, direction)
    window = df.tail(lookback).copy()
    ema_window = ema_series.tail(lookback)

    if direction == "LONG":
        penetrations = (ema_window - window["low"].astype(float)).clip(lower=0)
        positive_penetrations = penetrations[penetrations > 0]
        average_penetration = float(positive_penetrations.mean()) if not positive_penetrations.empty else 0.0
        stop = reference_price - (average_penetration * coefficient)
    else:
        penetrations = (window["high"].astype(float) - ema_window).clip(lower=0)
        positive_penetrations = penetrations[penetrations > 0]
        average_penetration = float(positive_penetrations.mean()) if not positive_penetrations.empty else 0.0
        stop = reference_price + (average_penetration * coefficient)

    return stop, average_penetration


def calc_pullback_pivot_stop(df: pd.DataFrame, direction: str) -> float | None:
    if df is None or df.empty:
        return None

    window = df.tail(DAILY_CORRECTION_WINDOW_MAX)
    if direction == "LONG":
        return float(window["low"].min())
    return float(window["high"].max())


def calc_two_bar_stop(df: pd.DataFrame, direction: str) -> float | None:
    if df is None or df.empty:
        return None

    window = df.tail(2)
    if direction == "LONG":
        return float(window["low"].min())
    return float(window["high"].max())


def calc_nick_stop(df: pd.DataFrame, direction: str, offset: float = NICK_STOP_OFFSET) -> float | None:
    detail = calc_nick_stop_detail(df, direction, offset=offset)
    return detail["stop"] if detail else None


def calc_nick_stop_detail(df: pd.DataFrame, direction: str, offset: float = NICK_STOP_OFFSET) -> dict | None:
    if df is None or len(df) < 2:
        return None

    direction = str(direction).upper()
    if direction not in {"LONG", "SHORT"}:
        return None

    structure_window = df.tail(NICK_STOP_LOOKBACK_DAYS)

    if direction == "LONG":
        probe_prices = structure_window["low"].astype(float).nsmallest(min(2, len(structure_window)))
        if probe_prices.empty:
            return None
        reference_position = 1 if len(probe_prices) > 1 else 0
        reference_price = float(probe_prices.iloc[reference_position])
        reference_at = probe_prices.index[reference_position]
        stop = reference_price - offset
        return {
            "stop": stop,
            "reference_price": reference_price,
            "reference_date": str(pd.Timestamp(reference_at).date()),
            "reference_at": str(reference_at),
            "reference_rank": "second_low" if reference_position == 1 else "lowest",
        }

    probe_prices = structure_window["high"].astype(float).nlargest(min(2, len(structure_window)))
    if probe_prices.empty:
        return None
    reference_position = 1 if len(probe_prices) > 1 else 0
    reference_price = float(probe_prices.iloc[reference_position])
    reference_at = probe_prices.index[reference_position]
    stop = reference_price + offset
    return {
        "stop": stop,
        "reference_price": reference_price,
        "reference_date": str(pd.Timestamp(reference_at).date()),
        "reference_at": str(reference_at),
        "reference_rank": "second_high" if reference_position == 1 else "highest",
    }


def calc_ema_penetration_entry_plan(
    df: pd.DataFrame | None,
    direction: str,
    ema_period: int = DAILY_EMA_PERIOD,
    lookback: int = ENTRY_PENETRATION_LOOKBACK,
    min_tick: float = MIN_TICK,
) -> dict:
    if df is None or len(df) < max(ema_period + 2, 3) or direction not in {"LONG", "SHORT"}:
        return {"available": False, "reason": "Insufficient daily data; cannot calculate EMA penetration entry"}

    frame = df.copy()
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    ema = calc_ema(close, ema_period)
    ema_now = float(ema.iloc[-1])
    ema_prev = float(ema.iloc[-2])
    ema_slope = ema_now - ema_prev
    projected_ema = ema_now + ema_slope
    window = frame.tail(max(lookback, 1))
    ema_window = ema.tail(max(lookback, 1))

    if direction == "LONG":
        penetrations = (ema_window - window["low"].astype(float)).clip(lower=0)
        positive = penetrations[penetrations > 0]
        average_penetration = float(positive.mean()) if not positive.empty else 0.0
        ema_penetration_entry = projected_ema - average_penetration
        breakout_entry = float(high.iloc[-1]) + min_tick
        trigger_label = "Buy"
        reason = (
            f"Projected next EMA{ema_period} estimate {projected_ema:.2f}, minus recent {lookback}-day average downside penetration "
            f"{average_penetration:.2f}, reference buy limit {ema_penetration_entry:.2f}; "
            f"alternate buy-stop is above the previous day high at {breakout_entry:.2f}."
        )
    else:
        penetrations = (window["high"].astype(float) - ema_window).clip(lower=0)
        positive = penetrations[penetrations > 0]
        average_penetration = float(positive.mean()) if not positive.empty else 0.0
        ema_penetration_entry = projected_ema + average_penetration
        breakout_entry = float(low.iloc[-1]) - min_tick
        trigger_label = "Short sell"
        reason = (
            f"Projected next EMA{ema_period} estimate {projected_ema:.2f}, plus recent {lookback}-day average upside penetration "
            f"{average_penetration:.2f}, reference short-sell limit {ema_penetration_entry:.2f}; "
            f"alternate sell-stop is below the previous day low at {breakout_entry:.2f}."
        )

    return {
        "available": True,
        "direction": direction,
        "ema_period": ema_period,
        "lookback": lookback,
        "min_tick": min_tick,
        "latest_ema": round(ema_now, 4),
        "ema_prev": round(ema_prev, 4),
        "ema_slope": round(ema_slope, 4),
        "projected_next_ema": round(projected_ema, 4),
        "average_penetration": round(average_penetration, 4),
        "ema_penetration_entry": round(ema_penetration_entry, 4),
        "breakout_entry": round(breakout_entry, 4),
        "previous_high": round(float(high.iloc[-1]), 4),
        "previous_low": round(float(low.iloc[-1]), 4),
        "trigger_label": trigger_label,
        "reason": reason,
    }


def calc_weekly_value_target(df_week: pd.DataFrame | None, direction: str) -> dict:
    if df_week is None or len(df_week) < WEEKLY_VALUE_SLOW_EMA + 2 or direction not in {"LONG", "SHORT"}:
        return {"available": False, "target_price": None, "reason": "Insufficient weekly data; cannot calculate weekly value-zone target"}

    close = df_week["close"].astype(float)
    fast = calc_ema(close, WEEKLY_VALUE_FAST_EMA)
    slow = calc_ema(close, WEEKLY_VALUE_SLOW_EMA)
    lower = min(float(fast.iloc[-1]), float(slow.iloc[-1]))
    upper = max(float(fast.iloc[-1]), float(slow.iloc[-1]))
    target = upper if direction == "LONG" else lower
    return {
        "available": True,
        "fast_ema_period": WEEKLY_VALUE_FAST_EMA,
        "slow_ema_period": WEEKLY_VALUE_SLOW_EMA,
        "fast_ema": round(float(fast.iloc[-1]), 4),
        "slow_ema": round(float(slow.iloc[-1]), 4),
        "value_zone_low": round(lower, 4),
        "value_zone_high": round(upper, 4),
        "target_price": round(target, 4),
        "reason": (
            f"Weekly value zone EMA{WEEKLY_VALUE_FAST_EMA}/{WEEKLY_VALUE_SLOW_EMA}: "
            f"{lower:.2f}~{upper:.2f}; {'Long' if direction == 'LONG' else 'Short'} first profit target "
            f"{target:.2f}."
        ),
    }


def calc_atr_stops(
    df: pd.DataFrame,
    direction: str,
    atr_period: int,
    multipliers: tuple[float, ...] = (1.0, 2.0),
) -> tuple[dict[float, float | None], float]:
    if df is None or len(df) < 2:
        return {multiplier: None for multiplier in multipliers}, 0.0

    direction = str(direction).upper()
    if direction not in {"LONG", "SHORT"}:
        return {multiplier: None for multiplier in multipliers}, 0.0

    atr_value = float(calc_atr(df, atr_period).iloc[-1])
    latest_high = float(df["high"].iloc[-1])
    latest_low = float(df["low"].iloc[-1])
    stops: dict[float, float | None] = {}

    for multiplier in multipliers:
        if direction == "LONG":
            stops[multiplier] = latest_low - (atr_value * multiplier)
        else:
            stops[multiplier] = latest_high + (atr_value * multiplier)

    return stops, atr_value


def calc_chandelier_stop(
    df: pd.DataFrame,
    direction: str,
    atr_period: int,
    lookback: int = CHANDELIER_LOOKBACK,
    atr_multiplier: float = CHANDELIER_ATR_MULTIPLIER,
) -> float | None:
    if df is None or len(df) < max(atr_period, 2):
        return None

    atr = float(calc_atr(df, atr_period).iloc[-1])
    if direction == "LONG":
        reference = float(df["high"].astype(float).tail(lookback).max())
        return reference - (atr * atr_multiplier)

    reference = float(df["low"].astype(float).tail(lookback).min())
    return reference + (atr * atr_multiplier)


def calc_parabolic_stop(
    df: pd.DataFrame,
    direction: str,
    step: float = PARABOLIC_STEP,
    max_step: float = PARABOLIC_MAX_STEP,
) -> float | None:
    if df is None or len(df) < 2 or direction not in {"LONG", "SHORT"}:
        return None

    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    uptrend = direction == "LONG"
    sar = lows[0] if uptrend else highs[0]
    extreme_point = highs[0] if uptrend else lows[0]
    acceleration_factor = step

    for index in range(1, len(highs)):
        sar = sar + acceleration_factor * (extreme_point - sar)
        if uptrend:
            prior_lows = lows[max(0, index - 2) : index]
            if prior_lows:
                sar = min([sar, *prior_lows])
            if lows[index] < sar:
                uptrend = False
                sar = extreme_point
                extreme_point = lows[index]
                acceleration_factor = step
            else:
                if highs[index] > extreme_point:
                    extreme_point = highs[index]
                    acceleration_factor = min(acceleration_factor + step, max_step)
        else:
            prior_highs = highs[max(0, index - 2) : index]
            if prior_highs:
                sar = max([sar, *prior_highs])
            if highs[index] > sar:
                uptrend = True
                sar = extreme_point
                extreme_point = highs[index]
                acceleration_factor = step
            else:
                if lows[index] < extreme_point:
                    extreme_point = lows[index]
                    acceleration_factor = min(acceleration_factor + step, max_step)

    return float(sar)


def build_stop_methods(
    df: pd.DataFrame | None,
    direction: str,
    trade_plan: TradePlanConfig,
    atr_period: int,
    signal_bar_high: float | None = None,
    signal_bar_low: float | None = None,
) -> list[dict]:
    if df is None or df.empty or direction not in {"LONG", "SHORT"}:
        return []

    safezone_stop, safezone_noise = calc_safezone_stop(df, direction, trade_plan)
    nick_detail = calc_nick_stop_detail(df, direction)
    nick_stop = nick_detail["stop"] if nick_detail else None
    atr_stops, atr_value = calc_atr_stops(df, direction, atr_period=atr_period)
    atr_stop_1x = atr_stops.get(1.0)
    atr_stop_2x = atr_stops.get(2.0)

    return [
        {
            "code": "NICK",
            "group": "initial",
            "label": "Nick stop",
            "price": round(nick_stop, 4) if nick_stop is not None else None,
            "reference": (
                f"{nick_detail.get('reference_date')} "
                f"{nick_detail.get('reference_price'):.2f} plus 0.01 outside"
                if nick_detail
                else "second low/high of current V/W structure plus 0.01 outside"
            ),
            "reference_date": nick_detail.get("reference_date") if nick_detail else None,
            "reference_price": round(float(nick_detail["reference_price"]), 4) if nick_detail else None,
            "style": "structure",
            "source": "prior-low/second-low structural stop",
            "suitable_for": "Useful when bottom/top structure is clear and you want slight noise outside the extreme.",
            "detail": "From the latest opposite pivot, use the second low/high in the current structure plus one cent outside.",
        },
        {
            "code": "SAFEZONE",
            "group": "initial",
            "label": "SafeZone initial stop",
            "price": round(safezone_stop, 4) if safezone_stop is not None else None,
            "reference": (
                f"EMA{trade_plan.safezone_ema_period} last {trade_plan.safezone_lookback}bars penetration average × "
                f"{_safezone_coefficient(trade_plan, direction)}"
            ),
            "style": "adaptive",
            "source": "Elder official SafeZone",
            "suitable_for": "Useful when entry risk should allow trend noise.",
            "detail": f"Initial defense estimated from EMA penetration noise; current average penetration {safezone_noise:.4f}.",
        },
        {
            "code": "ATR_1X",
            "group": "trailing",
            "label": "ATR trailing stop 1x",
            "price": round(atr_stop_1x, 4) if atr_stop_1x is not None else None,
            "reference": f"Latest daily extreme +/- 1.0 ATR (current ATR {atr_value:.4f})",
            "style": "adaptive",
            "source": "ATR trailing stop",
            "suitable_for": "Useful for tighter trailing stops.",
            "detail": "Placed one ATR outside the latest daily bar extreme.",
        },
        {
            "code": "ATR_2X",
            "group": "trailing",
            "label": "ATR trailing stop 2x",
            "price": round(atr_stop_2x, 4) if atr_stop_2x is not None else None,
            "reference": f"Latest daily extreme +/- 2.0 ATR (current ATR {atr_value:.4f})",
            "style": "adaptive",
            "source": "ATR trailing stop",
            "suitable_for": "Useful when you want more room for position volatility.",
            "detail": "Placed two ATR outside the latest daily bar extreme as a wider trailing stop.",
        },
    ]


def screen_weekly(df_week: pd.DataFrame | None, settings: StrategyConfig) -> dict:
    required = settings.weekly.macd_slow + settings.weekly.macd_signal + 5
    if df_week is None or len(df_week) < required:
        return {"trend": "NEUTRAL", "pass": False, "actionable": False, "reason": "Insufficient weekly data"}

    macd, signal, histogram = calc_macd(df_week, settings)
    impulse = calc_impulse_system(df_week, settings)
    macd_now = float(macd.iloc[-1])
    macd_prev = float(macd.iloc[-2])
    macd_slope = macd_now - macd_prev
    hist_now = float(histogram.iloc[-1])
    hist_prev = float(histogram.iloc[-2])
    hist_delta = hist_now - hist_prev
    macd_deltas = macd.diff().dropna()

    confirmed = 0
    for value in reversed(macd_deltas.values):
        if (macd_slope > 0 and value > 0) or (macd_slope < 0 and value < 0):
            confirmed += 1
        else:
            break

    if macd_slope > 0:
        trend = "LONG"
    elif macd_slope < 0:
        trend = "SHORT"
    else:
        trend = "NEUTRAL"

    impulse_color = impulse["color"]
    impulse_direction = impulse["direction"]
    impulse_state = "RISING" if macd_slope > 0 else "FALLING" if macd_slope < 0 else "FLAT"
    actionable = trend != "NEUTRAL"
    confirmed_pass = confirmed >= settings.weekly.confirm_bars
    allows_long = impulse_color != "RED"
    allows_short = impulse_color != "GREEN"
    impulse_allows_direction = (
        (trend == "LONG" and allows_long)
        or (trend == "SHORT" and allows_short)
        or trend == "NEUTRAL"
    )
    impulse_aligned = impulse_direction == trend
    close_on_trend_side = True
    trend_score = 0.0
    if actionable:
        trend_score += min(abs(float(macd_slope)) * 40, 2.5)
        trend_score += min(confirmed, 4) * 0.35
        if impulse_aligned:
            trend_score += 0.8
        elif impulse_allows_direction:
            trend_score += 0.35
        if (trend == "LONG" and hist_now < 0) or (trend == "SHORT" and hist_now > 0):
            trend_score += 0.8

    if trend == "LONG":
        setup_state = "BULLISH_MACD_SLOPE"
        reason = (
            f"Weekly MACD slope {macd_slope:+.4f} (up); "
            f"Impulse system {impulse_color} (EMA slope {impulse['ema_slope']:+.4f}, MACD slope {impulse['macd_slope']:+.4f}); "
            f"confirmed bars {confirmed}/{settings.weekly.confirm_bars}, allows_long={allows_long}."
        )
    elif trend == "SHORT":
        setup_state = "BEARISH_MACD_SLOPE"
        reason = (
            f"Weekly MACD slope {macd_slope:+.4f} (down); "
            f"Impulse system {impulse_color} (EMA slope {impulse['ema_slope']:+.4f}, MACD slope {impulse['macd_slope']:+.4f}); "
            f"confirmed bars {confirmed}/{settings.weekly.confirm_bars}, allows_short={allows_short}."
        )
    else:
        setup_state = "NEUTRAL"
        reason = (
            f"Weekly MACD slope {macd_slope:+.4f} (unclear direction); "
            f"Impulse system {impulse_color}; confirmed bars {confirmed}/{settings.weekly.confirm_bars}."
        )

    value_target = calc_weekly_value_target(df_week, trend)

    return {
        "trend": trend,
        "impulse": impulse_state,
        "impulse_color": impulse_color,
        "impulse_direction": impulse_direction,
        "allows_long": allows_long,
        "allows_short": allows_short,
        "setup_state": setup_state,
        "histogram": round(float(hist_now), 6),
        "histogram_prev": round(float(hist_prev), 6),
        "histogram_delta": round(float(hist_delta), 6),
        "histogram_strength": abs(float(hist_now)),
        "histogram_growing": hist_delta > 0,
        "macd": round(float(macd_now), 6),
        "macd_prev": round(float(macd_prev), 6),
        "macd_slope": round(float(macd_slope), 6),
        "macd_signal": round(float(signal.iloc[-1]), 6),
        "ema13": impulse["ema"],
        "ema13_prev": impulse["ema_prev"],
        "ema13_slope": impulse["ema_slope"],
        "confirmed_bars": confirmed,
        "impulse_aligned": impulse_aligned,
        "impulse_allows_direction": impulse_allows_direction,
        "close_on_trend_side": close_on_trend_side,
        "weekly_value_target": value_target,
        "trend_score": round(min(trend_score, WEEKLY_TREND_SCORE_CAP), 2),
        "actionable": actionable,
        "pass_checks": {
            "actionable": actionable,
            "confirmed_bars": confirmed_pass,
            "impulse_aligned": impulse_allows_direction if settings.weekly.require_impulse_alignment else True,
            "close_on_trend_side": close_on_trend_side,
        },
        "pass": (
            actionable
            and confirmed_pass
            and (impulse_allows_direction or not settings.weekly.require_impulse_alignment)
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
            "reject_reason": "Insufficient daily data",
            "reason": "Insufficient daily data",
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
    latest_open = float(df_day["open"].iloc[-1])
    latest_close = float(close.iloc[-1])
    latest_high = float(high.iloc[-1])
    latest_low = float(low.iloc[-1])
    ema13 = calc_ema(close, DAILY_EMA_PERIOD)
    atr_series = calc_atr(df_day, settings.hourly.atr_period)
    value_band_low, value_band_high, value_band_padding = calc_value_zone_bounds(
        ema13,
        atr_series,
        settings.daily.value_band_atr_multiplier,
    )
    _, _, macd_hist = calc_macd(df_day, settings)
    macd_hist_now = float(macd_hist.iloc[-1])
    macd_hist_prev = float(macd_hist.iloc[-2])
    rsi_delta = rsi_now - rsi_prev
    macd_hist_delta = macd_hist_now - macd_hist_prev
    force_ema = calc_force_index_ema(df_day, FORCE_INDEX_EMA_PERIOD)
    force_now = float(force_ema.iloc[-1])
    force_prev = float(force_ema.iloc[-2])
    force_delta = force_now - force_prev
    impulse = calc_impulse_system(df_day, settings)
    impulse_color = impulse["color"]
    entry_plan = calc_ema_penetration_entry_plan(df_day, trend)
    prior_closes = close.iloc[-DAILY_CORRECTION_WINDOW_MAX:]
    down_closes = int((prior_closes.diff() < 0).sum())
    up_closes = int((prior_closes.diff() > 0).sum())
    candle_range = max(float(latest_high - latest_low), 1e-9)
    close_location_pct = ((latest_close - latest_low) / candle_range) * 100
    close_above_prev = bool(latest_close > close.iloc[-2])
    close_below_prev = bool(latest_close < close.iloc[-2])
    custom_close_rule_pass = close_above_prev if trend == "LONG" else close_below_prev if trend == "SHORT" else False
    custom_wick_rule_pass = False
    custom_close_location_rule_pass = (
        latest_close >= (latest_low + candle_range * 0.5)
        if trend == "LONG"
        else latest_close <= (latest_high - candle_range * 0.5)
        if trend == "SHORT"
        else False
    )
    if trend == "LONG":
        wick_ratio_pct = (float(min(latest_close, latest_open) - latest_low) / candle_range) * 100
        custom_wick_rule_pass = bool(wick_ratio_pct >= 35.0)
    elif trend == "SHORT":
        wick_ratio_pct = (float(latest_high - max(latest_close, latest_open)) / candle_range) * 100
        custom_wick_rule_pass = bool(wick_ratio_pct >= 35.0)
    else:
        wick_ratio_pct = 0.0
    custom_kline_confirmation = bool(
        custom_close_rule_pass and custom_wick_rule_pass and custom_close_location_rule_pass
    )

    force_high_window = force_ema.shift(1).tail(FORCE_INDEX_NEW_EXTREME_LOOKBACK)
    force_not_new_high = bool(force_high_window.empty or force_now < float(force_high_window.max()))
    entered_value_zone = bool(latest_low <= float(value_band_high.iloc[-1]) and latest_high >= float(value_band_low.iloc[-1]))
    value_zone_reached = entered_value_zone
    structure_break_level = None
    structure_intact = False
    countertrend_exists = False
    force_signal = False
    same_impulse_or_trend = False
    watch = False
    passed = False
    reject_reason = ""
    state = "REJECT"
    rsi_state = "NEUTRAL"
    correction_count = 0
    correction_counter_label = "recent correction closes"
    histogram_reversal = False
    rsi_strength = 0.0

    if trend == "LONG":
        correction_count = down_closes
        correction_counter_label = "recent down closes"
        countertrend_exists = bool(force_now < 0 or down_closes >= 1 or latest_low <= float(ema13.iloc[-1]))
        force_signal = force_now < 0
        same_impulse_or_trend = impulse_color in {"GREEN", "BLUE"}
        higher_low_ref = float(low.tail(DAILY_CORRECTION_WINDOW_MAX).min())
        structure_break_level = higher_low_ref - (float(atr_series.iloc[-1]) * DAILY_STRUCTURE_BREACH_ATR_MULTIPLIER)
        structure_intact = bool(latest_low >= structure_break_level)
        histogram_reversal = bool(macd_hist_now > macd_hist_prev)
        rsi_strength = max(0.0, 50.0 - rsi_now)
        if not force_signal:
            state = "REJECT"
            reject_reason = "Weekly allows long, but 2-day Force Index EMA is not below zero"
            rsi_state = "PULLBACK_WAIT_FORCE_BELOW_ZERO"
        elif not same_impulse_or_trend:
            state = "WATCH"
            watch = True
            reject_reason = "Daily impulse is still red; watch first, not an immediate trade signal"
            rsi_state = "PULLBACK_FORCE_READY_WAIT_IMPULSE"
        elif not structure_intact:
            state = "WATCH"
            watch = True
            reject_reason = "Force signal appeared, but the daily low broke short-term structure; watch carefully"
            rsi_state = "STRUCTURE_BROKEN"
        else:
            state = "QUALIFIED"
            passed = True
            rsi_state = "PULLBACK_FORCE_BELOW_ZERO"
    elif trend == "SHORT":
        correction_count = up_closes
        correction_counter_label = "recent up closes"
        countertrend_exists = bool(force_now > 0 or up_closes >= 1 or latest_high >= float(ema13.iloc[-1]))
        force_signal = force_now > 0 and force_not_new_high
        same_impulse_or_trend = impulse_color in {"RED", "BLUE"}
        lower_high_ref = float(high.tail(DAILY_CORRECTION_WINDOW_MAX).max())
        structure_break_level = lower_high_ref + (float(atr_series.iloc[-1]) * DAILY_STRUCTURE_BREACH_ATR_MULTIPLIER)
        structure_intact = bool(latest_high <= structure_break_level)
        histogram_reversal = bool(macd_hist_now < macd_hist_prev)
        rsi_strength = max(0.0, rsi_now - 50.0)
        if not force_signal:
            state = "REJECT"
            reject_reason = "Weekly allows short, but 2-day Force Index EMA is not above zero or has made a multi-week high"
            rsi_state = "RALLY_WAIT_FORCE_ABOVE_ZERO"
        elif not same_impulse_or_trend:
            state = "WATCH"
            watch = True
            reject_reason = "Daily impulse is still green; watch first, not an immediate trade signal"
            rsi_state = "RALLY_FORCE_READY_WAIT_IMPULSE"
        elif not structure_intact:
            state = "WATCH"
            watch = True
            reject_reason = "Force signal appeared, but the daily high broke short-term structure; watch carefully"
            rsi_state = "STRUCTURE_BROKEN"
        else:
            state = "QUALIFIED"
            passed = True
            rsi_state = "RALLY_FORCE_ABOVE_ZERO"
    else:
        reject_reason = "Weekly direction is unclear; daily setup is not traded independently"

    elder_core_checks = [force_signal, same_impulse_or_trend, structure_intact]
    setup_score = (
        1.0
        + (1.7 if force_signal else 0.0)
        + (1.2 if same_impulse_or_trend else 0.0)
        + (0.8 if structure_intact else 0.0)
        + (0.4 if entered_value_zone else 0.0)
        + (0.2 if custom_kline_confirmation else 0.0)
    )

    elder_core_signal_count = int(sum(elder_core_checks))
    if trend == "LONG":
        direction_label = "pullback"
    elif trend == "SHORT":
        direction_label = "rally"
    else:
        direction_label = "correction"

    value_zone_label = (
        f"{value_band_low.iloc[-1]:.2f}~{value_band_high.iloc[-1]:.2f}, latest range {latest_low:.2f}~{latest_high:.2f}"
        if trend in {"LONG", "SHORT"}
        else "—"
    )
    structure_label = (
        f"Latest low {latest_low:.2f} >= defense level {structure_break_level:.2f}" if trend == "LONG" and structure_break_level is not None
        else f"Latest high {latest_high:.2f} <= defense level {structure_break_level:.2f}" if trend == "SHORT" and structure_break_level is not None
        else "—"
    )
    histogram_label = f"Histogram {macd_hist_prev:+.4f}->{macd_hist_now:+.4f}" if trend in {"LONG", "SHORT"} else "—"
    if trend == "LONG":
        value_band_gap = max(latest_close - float(value_band_high.iloc[-1]), 0.0)
    elif trend == "SHORT":
        value_band_gap = max(float(value_band_low.iloc[-1]) - latest_close, 0.0)
    else:
        value_band_gap = None
    detail_prefix = (
        f"{correction_counter_label} {correction_count}; "
        f"2-day Force EMA {force_prev:+.0f}->{force_now:+.0f}; "
        f"Daily impulse system {impulse_color}; "
        f"13EMA value band {value_zone_label}; "
        f"{structure_label}; "
        f"Auxiliary {histogram_label}."
    )

    if state == "REJECT":
        reason = f"{detail_prefix} Conclusion: {reject_reason}"
    elif state == "QUALIFIED":
        reason = (
            f"{detail_prefix} Conclusion: {direction_label} setup is executable; "
            f"{elder_core_signal_count}/3 triple-screen core signals are ready; candidate can enter the pool and wait for price trigger."
        )
    else:
        reason = (
            f"{detail_prefix} Conclusion: {direction_label} setup exists, but only {elder_core_signal_count}/3 core signals are ready; keep watching."
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
        "value_band_gap": round(float(value_band_gap), 4) if value_band_gap is not None else None,
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
        "force_index_ema2": round(float(force_now), 4) if trend in {"LONG", "SHORT"} else 0.0,
        "force_index_ema2_prev": round(float(force_prev), 4) if trend in {"LONG", "SHORT"} else 0.0,
        "force_index_delta": round(float(force_delta), 4) if trend in {"LONG", "SHORT"} else 0.0,
        "force_signal": force_signal if trend in {"LONG", "SHORT"} else False,
        "force_not_new_high": force_not_new_high if trend == "SHORT" else None,
        "impulse_color": impulse_color,
        "impulse_direction": impulse.get("direction"),
        "impulse_ema_slope": impulse.get("ema_slope"),
        "impulse_macd_slope": impulse.get("macd_slope"),
        "same_impulse_or_trend": same_impulse_or_trend,
        "entry_plan": entry_plan,
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
        return {"pass": False, "reason": "Insufficient hourly data"}

    closed_bars, live_bar, live_bar_available = _split_hourly_execution_bars(df_hour, as_of=as_of)
    minimum_closed = settings.hourly.atr_period + 1
    if len(closed_bars) < minimum_closed:
        return {"pass": False, "reason": "Insufficient closed hourly data"}

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
            f"Current hourly bar broke above the previous closed hourly high; trailing buy-stop triggered (strength {breakout_strength:.2f} ATR)"
            if passed
            else (
                f"Hourly has not triggered; current buy-stop tracks above the previous closed hourly high (distance {trigger_gap:.2f}, about {gap_atr:.2f} ATR)"
                if live_bar_available
                else "No active hourly bar; the next hourly bar will continue moving the buy-stop above the latest closed hourly high"
            )
        )
    elif trend == "SHORT":
        reason = (
            f"Current hourly bar broke below the previous closed hourly low; trailing sell-stop triggered (strength {breakout_strength:.2f} ATR)"
            if passed
            else (
                f"Hourly has not triggered; current sell-stop tracks below the previous closed hourly low (distance {trigger_gap:.2f}, about {gap_atr:.2f} ATR)"
                if live_bar_available
                else "No active hourly bar; the next hourly bar will continue moving the sell-stop below the latest closed hourly low"
            )
        )
    else:
        reason = "Hourly direction does not match weekly direction"

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
    weekly_frame: pd.DataFrame | None = None,
    hourly_frame: pd.DataFrame | None = None,
) -> dict:
    weekly_target = calc_weekly_value_target(weekly_frame, direction)
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
        nick_detail = None
        hourly_safezone_stop = None
    else:
        latest_high = float(daily_frame["high"].iloc[-1])
        latest_low = float(daily_frame["low"].iloc[-1])
        safezone_stop, safezone_noise = calc_safezone_stop(daily_frame, direction, trade_plan)
        hourly_safezone_stop, _ = calc_safezone_stop(hourly_frame, direction, trade_plan) if hourly_frame is not None and not hourly_frame.empty else (None, 0.0)
        nick_detail = calc_nick_stop_detail(daily_frame, direction)
        nick_stop = nick_detail["stop"] if nick_detail else None
        atr_stops, daily_atr = calc_atr_stops(daily_frame, direction, atr_period=14)
        atr_stop_1x = atr_stops.get(1.0)
        atr_stop_2x = atr_stops.get(2.0)
        stop_methods = build_stop_methods(
            daily_frame,
            direction,
            trade_plan,
            atr_period=14,
            signal_bar_high=signal_bar_high,
            signal_bar_low=signal_bar_low,
        )
        temperature, average_temperature = calc_market_thermometer(daily_frame, trade_plan.thermometer_period)
        thermometer = float(temperature.iloc[-1])
        thermometer_ema = float(average_temperature.iloc[-1])
        projected_move = thermometer_ema * trade_plan.thermometer_target_multiplier

        # Temporarily disabled legacy SIGNAL_BAR / TWO_BAR / PULLBACK_PIVOT / CHANDELIER / PARABOLIC exits.
        # Initial stops keep SafeZone and Nick; trailing stops keep ATR 1x / 2x.
        if direction == "LONG":
            initial_candidates = [("SAFEZONE", safezone_stop), ("NICK", nick_stop)]
            valid_initial_candidates = [(code, float(value)) for code, value in initial_candidates if value is not None]
            if valid_initial_candidates:
                model_initial_stop_basis, model_initial_stop_loss = min(valid_initial_candidates, key=lambda item: item[1])
            else:
                model_initial_stop_basis, model_initial_stop_loss = "UNKNOWN", entry
            initial_stop_basis = "CHOICE_REQUIRED"
            initial_stop_loss = None
            protective_stop_loss = atr_stop_1x if atr_stop_1x is not None else model_initial_stop_loss
            protective_stop_basis = "ATR_1X" if atr_stop_1x is not None else model_initial_stop_basis
            stop_loss = None
            stop_basis = "CHOICE_REQUIRED"
            target_reference = max(entry, latest_high)
            weekly_target_price = (
                float(weekly_target["target_price"])
                if weekly_target.get("available") and weekly_target.get("target_price") is not None
                else None
            )
            take_profit = (
                weekly_target_price
                if weekly_target_price is not None and weekly_target_price > entry
                else target_reference + projected_move
            )
        else:
            initial_candidates = [("SAFEZONE", safezone_stop), ("NICK", nick_stop)]
            valid_initial_candidates = [(code, float(value)) for code, value in initial_candidates if value is not None]
            if valid_initial_candidates:
                model_initial_stop_basis, model_initial_stop_loss = max(valid_initial_candidates, key=lambda item: item[1])
            else:
                model_initial_stop_basis, model_initial_stop_loss = "UNKNOWN", entry
            initial_stop_basis = "CHOICE_REQUIRED"
            initial_stop_loss = None
            protective_stop_loss = atr_stop_1x if atr_stop_1x is not None else model_initial_stop_loss
            protective_stop_basis = "ATR_1X" if atr_stop_1x is not None else model_initial_stop_basis
            stop_loss = None
            stop_basis = "CHOICE_REQUIRED"
            target_reference = min(entry, latest_low)
            weekly_target_price = (
                float(weekly_target["target_price"])
                if weekly_target.get("available") and weekly_target.get("target_price") is not None
                else None
            )
            take_profit = (
                weekly_target_price
                if weekly_target_price is not None and weekly_target_price < entry
                else target_reference - projected_move
            )

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
        "initial_stop_hourly_safezone": round(hourly_safezone_stop, 4) if hourly_safezone_stop is not None else None,
        "initial_stop_nick": round(nick_stop, 4) if nick_stop is not None else None,
        "initial_stop_nick_reference_date": nick_detail.get("reference_date") if nick_detail else None,
        "initial_stop_nick_reference_price": (
            round(float(nick_detail["reference_price"]), 4) if nick_detail else None
        ),
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
        "weekly_value_target": weekly_target,
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
    score += calc_reward_risk_score(float(exits.get("reward_risk_ratio_model", exits.get("reward_risk_ratio", 0.0)) or 0.0))

    if weekly_result.get("pass"):
        score += 0.4
    if daily_result.get("pass"):
        score += 0.4
    if hourly_result.get("pass"):
        score += 0.6

    return round(min(score, 10), 2)


def calc_signal_score(weekly_result: dict, daily_result: dict, hourly_result: dict, exits: dict) -> float:
    return calc_execution_score(weekly_result, daily_result, hourly_result, exits)
