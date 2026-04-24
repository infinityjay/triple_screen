from __future__ import annotations

import argparse
import csv
import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

from clients.alpaca import AlpacaClient
from config.loader import load_settings

logger = logging.getLogger(__name__)


DEFAULT_RULES: dict[str, float | int] = {
    "minimum_price": 10.0,
    "minimum_avg_dollar_volume_20d": 50_000_000.0,
    "minimum_history_days": 252,
    "minimum_atr_pct": 0.025,
    "maximum_atr_pct": 0.12,
    "target_atr_pct": 0.045,
    "atr_fit_band": 0.035,
    "lookback_1m": 21,
    "lookback_6m": 126,
    "lookback_12m": 252,
    "ema_fast": 50,
    "ema_slow": 200,
    "atr_period": 14,
    "fetch_buffer_days": 420,
}

CORE_COMPONENT_WEIGHTS: dict[str, float] = {
    "momentum": 0.35,
    "relative_strength": 0.20,
    "trend_quality": 0.15,
    "atr_fit": 0.15,
    "liquidity": 0.15,
}

OPTIONAL_COMPONENT_WEIGHTS: dict[str, float] = {
    "quality": 0.15,
    "earnings_revision": 0.10,
    "short_crowding_penalty": -0.10,
}


@dataclass(frozen=True)
class SymbolSource:
    label: str
    rows: list[dict[str, Any]]


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optimize a stock universe into a smaller ranked candidate set.")
    parser.add_argument("--config", default=None, help="Path to settings YAML. Defaults to config/settings.yaml.")
    parser.add_argument("--input-file", default=None, help="Local YAML/JSON/CSV file with symbol rows.")
    parser.add_argument("--input-url", default=None, help="Remote YAML/JSON/CSV URL with symbol rows.")
    parser.add_argument(
        "--input-format",
        choices=("auto", "yaml", "json", "csv"),
        default="auto",
        help="Explicit input format override for --input-file/--input-url.",
    )
    parser.add_argument("--top-k", type=int, default=100, help="Final selected symbol count.")
    parser.add_argument(
        "--long-count",
        type=int,
        default=0,
        help="Optional minimum long candidate count. Default 0 means no side quota.",
    )
    parser.add_argument(
        "--short-count",
        type=int,
        default=0,
        help="Optional minimum short candidate count. Default 0 means no side quota.",
    )
    parser.add_argument(
        "--output-file",
        default="config/universe_us_top100_optimized.yaml",
        help="Output YAML path.",
    )
    parser.add_argument(
        "--benchmarks",
        default="SPY,QQQ",
        help="Comma-separated benchmark symbols used for relative-strength scoring.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of symbols per Alpaca daily-bar batch request.",
    )
    return parser


def _auto_detect_format(path_or_url: str) -> str:
    lowered = path_or_url.lower()
    if lowered.endswith(".yaml") or lowered.endswith(".yml"):
        return "yaml"
    if lowered.endswith(".json"):
        return "json"
    if lowered.endswith(".csv"):
        return "csv"
    return "yaml"


def _normalize_symbol(raw: Any) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().upper()
    return value or None


def _try_float(raw: Any) -> float | None:
    if raw in (None, "", "NA", "N/A", "null"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _ranked_symbol_dict(row: dict[str, Any], fallback_rank: int) -> dict[str, Any]:
    symbol = _normalize_symbol(row.get("ticker") or row.get("symbol") or row.get("code"))
    if not symbol:
        raise ValueError(f"Missing symbol in row: {row}")

    normalized = dict(row)
    normalized["ticker"] = symbol
    normalized.setdefault("symbol", symbol)
    normalized.setdefault("rank", fallback_rank)
    return normalized


def _load_symbol_rows_from_yaml(text: str) -> list[dict[str, Any]]:
    payload = yaml.safe_load(text) or {}
    if isinstance(payload, dict):
        raw_rows = payload.get("symbols", [])
    elif isinstance(payload, list):
        raw_rows = payload
    else:
        raise ValueError("Unsupported YAML payload for symbol list.")

    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_rows, start=1):
        if isinstance(item, str):
            rows.append({"ticker": _normalize_symbol(item), "rank": idx})
            continue
        if not isinstance(item, dict):
            raise ValueError(f"Unsupported YAML symbol item: {item}")
        rows.append(_ranked_symbol_dict(item, idx))
    return rows


def _load_symbol_rows_from_json(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    if isinstance(payload, dict):
        raw_rows = payload.get("symbols", [])
    elif isinstance(payload, list):
        raw_rows = payload
    else:
        raise ValueError("Unsupported JSON payload for symbol list.")

    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_rows, start=1):
        if isinstance(item, str):
            rows.append({"ticker": _normalize_symbol(item), "rank": idx})
            continue
        if not isinstance(item, dict):
            raise ValueError(f"Unsupported JSON symbol item: {item}")
        rows.append(_ranked_symbol_dict(item, idx))
    return rows


def _load_symbol_rows_from_csv(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(StringIO(text))
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(reader, start=1):
        rows.append(_ranked_symbol_dict(dict(item), idx))
    return rows


def _parse_symbol_rows(text: str, fmt: str) -> list[dict[str, Any]]:
    if fmt == "yaml":
        return _load_symbol_rows_from_yaml(text)
    if fmt == "json":
        return _load_symbol_rows_from_json(text)
    if fmt == "csv":
        return _load_symbol_rows_from_csv(text)
    raise ValueError(f"Unsupported input format: {fmt}")


def _deduplicate_symbol_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        symbol = _normalize_symbol(row.get("ticker") or row.get("symbol"))
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized = dict(row)
        normalized["ticker"] = symbol
        normalized["symbol"] = symbol
        normalized["rank"] = int(row.get("rank", idx))
        deduped.append(normalized)
    return deduped


def load_symbol_source(settings, input_file: str | None, input_url: str | None, input_format: str) -> SymbolSource:
    if input_file and input_url:
        raise ValueError("Choose either --input-file or --input-url, not both.")

    if input_file:
        path = Path(input_file)
        text = path.read_text(encoding="utf-8")
        fmt = _auto_detect_format(path.name) if input_format == "auto" else input_format
        rows = _deduplicate_symbol_rows(_parse_symbol_rows(text, fmt))
        return SymbolSource(label=str(path), rows=rows)

    if input_url:
        response = requests.get(input_url, timeout=20)
        response.raise_for_status()
        fmt = _auto_detect_format(input_url) if input_format == "auto" else input_format
        rows = _deduplicate_symbol_rows(_parse_symbol_rows(response.text, fmt))
        return SymbolSource(label=input_url, rows=rows)

    if settings.universe.symbols:
        rows = _deduplicate_symbol_rows(list(settings.universe.symbols))
        label = str(settings.universe.static_file or settings.config_path)
        return SymbolSource(label=label, rows=rows)

    raise ValueError("No input universe found. Provide --input-file, --input-url, or configure universe.static_file.")


def _fetch_daily_frames(
    market_data: AlpacaClient,
    symbols: list[str],
    benchmark_symbols: list[str],
    batch_size: int,
) -> dict[str, pd.DataFrame]:
    unique_symbols = list(dict.fromkeys(symbols + benchmark_symbols))
    end = datetime.now(UTC)
    start = end - timedelta(days=int(DEFAULT_RULES["fetch_buffer_days"]))

    frames: dict[str, pd.DataFrame] = {}
    for offset in range(0, len(unique_symbols), batch_size):
        batch = unique_symbols[offset : offset + batch_size]
        fetched = market_data.fetch_bars_batch(batch, "1Day", start, end)
        frames.update(fetched)
    return frames


def _compute_atr(frame: pd.DataFrame, period: int) -> float | None:
    if frame.empty or len(frame) < period + 1:
        return None
    highs = frame["high"]
    lows = frame["low"]
    closes = frame["close"]
    prev_close = closes.shift(1)
    true_range = pd.concat(
        [
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(period).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else None


def _safe_return(newer: float, older: float) -> float | None:
    if older in (None, 0) or newer is None:
        return None
    return float(newer / older - 1.0)


def _fit_score(value: float | None, target: float, band: float) -> float | None:
    if value is None or band <= 0:
        return None
    return max(0.0, 1.0 - abs(value - target) / band)


def _extract_security_metrics(
    row: dict[str, Any],
    frame: pd.DataFrame,
    benchmark_frames: dict[str, pd.DataFrame],
) -> dict[str, Any] | None:
    minimum_history_days = int(DEFAULT_RULES["minimum_history_days"])
    if frame is None or frame.empty or len(frame) < minimum_history_days:
        return None

    close = frame["close"]
    latest_close = float(close.iloc[-1])
    if latest_close < float(DEFAULT_RULES["minimum_price"]):
        return None

    adv20 = float((frame["close"] * frame["volume"]).tail(20).mean())
    adv60 = float((frame["close"] * frame["volume"]).tail(60).mean())
    if adv20 < float(DEFAULT_RULES["minimum_avg_dollar_volume_20d"]):
        return None

    atr = _compute_atr(frame, int(DEFAULT_RULES["atr_period"]))
    atr_pct = (atr / latest_close) if atr and latest_close else None
    if atr_pct is None:
        return None
    if atr_pct < float(DEFAULT_RULES["minimum_atr_pct"]) or atr_pct > float(DEFAULT_RULES["maximum_atr_pct"]):
        return None

    lb1 = int(DEFAULT_RULES["lookback_1m"])
    lb6 = int(DEFAULT_RULES["lookback_6m"])
    lb12 = int(DEFAULT_RULES["lookback_12m"])
    if len(close) <= lb12:
        return None

    p_1m = float(close.iloc[-lb1])
    p_6m = float(close.iloc[-lb6])
    p_12m = float(close.iloc[-lb12])
    momentum_6m_ex_1m = _safe_return(p_1m, p_6m)
    momentum_12m_ex_1m = _safe_return(p_1m, p_12m)

    returns_126 = close.pct_change().tail(126)
    volatility_126 = float(returns_126.std() * math.sqrt(252)) if returns_126.notna().sum() >= 60 else None
    risk_adjusted_momentum_6m = (
        momentum_6m_ex_1m / volatility_126 if momentum_6m_ex_1m is not None and volatility_126 not in (None, 0) else None
    )
    risk_adjusted_momentum_12m = (
        momentum_12m_ex_1m / volatility_126
        if momentum_12m_ex_1m is not None and volatility_126 not in (None, 0)
        else None
    )

    ema_fast = frame["close"].ewm(span=int(DEFAULT_RULES["ema_fast"]), adjust=False).mean().iloc[-1]
    ema_slow = frame["close"].ewm(span=int(DEFAULT_RULES["ema_slow"]), adjust=False).mean().iloc[-1]
    ema_fast_gap = float(latest_close / ema_fast - 1.0) if ema_fast else None
    ema_slow_gap = float(latest_close / ema_slow - 1.0) if ema_slow else None
    ema_stack_gap = float(ema_fast / ema_slow - 1.0) if ema_slow else None

    high_252 = float(frame["high"].tail(252).max())
    low_252 = float(frame["low"].tail(252).min())
    range_span = high_252 - low_252
    range_position_252 = ((latest_close - low_252) / range_span) if range_span > 0 else None

    rs_metrics: dict[str, float | None] = {}
    for benchmark_symbol, benchmark_frame in benchmark_frames.items():
        benchmark_close = benchmark_frame["close"]
        if len(benchmark_close) <= lb12:
            return None
        benchmark_1m = float(benchmark_close.iloc[-lb1])
        benchmark_6m = float(benchmark_close.iloc[-lb6])
        benchmark_12m = float(benchmark_close.iloc[-lb12])
        benchmark_6m_ex_1m = _safe_return(benchmark_1m, benchmark_6m)
        benchmark_12m_ex_1m = _safe_return(benchmark_1m, benchmark_12m)
        rs_metrics[f"rs_{benchmark_symbol.lower()}_6m_ex_1m"] = (
            momentum_6m_ex_1m - benchmark_6m_ex_1m
            if momentum_6m_ex_1m is not None and benchmark_6m_ex_1m is not None
            else None
        )
        rs_metrics[f"rs_{benchmark_symbol.lower()}_12m_ex_1m"] = (
            momentum_12m_ex_1m - benchmark_12m_ex_1m
            if momentum_12m_ex_1m is not None and benchmark_12m_ex_1m is not None
            else None
        )

    return {
        "ticker": row["ticker"],
        "name": row.get("name"),
        "rank": int(row.get("rank", 0)),
        "sector": row.get("sector") or row.get("country"),
        "close": latest_close,
        "avg_dollar_volume_20d": adv20,
        "avg_dollar_volume_60d": adv60,
        "volume_expansion_20d_vs_60d": (adv20 / adv60) if adv60 else None,
        "atr_14d": atr,
        "atr_pct_14d": atr_pct,
        "momentum_6m_ex_1m": momentum_6m_ex_1m,
        "momentum_12m_ex_1m": momentum_12m_ex_1m,
        "risk_adjusted_momentum_6m": risk_adjusted_momentum_6m,
        "risk_adjusted_momentum_12m": risk_adjusted_momentum_12m,
        "volatility_126d": volatility_126,
        "ema_fast_gap": ema_fast_gap,
        "ema_slow_gap": ema_slow_gap,
        "ema_stack_gap": ema_stack_gap,
        "range_position_252d": range_position_252,
        "roe_ttm": _try_float(row.get("roe_ttm")),
        "debt_to_equity": _try_float(row.get("debt_to_equity")),
        "accruals_ratio": _try_float(row.get("accruals_ratio")),
        "earnings_revision_1m": _try_float(row.get("earnings_revision_1m")),
        "short_interest_pct_float": _try_float(row.get("short_interest_pct_float")),
        "days_to_cover": _try_float(row.get("days_to_cover")),
        **rs_metrics,
    }


def _winsorized_z(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    mean = numeric.mean()
    std = numeric.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series([0.0 if pd.notna(value) else math.nan for value in numeric], index=series.index)
    z = (numeric - mean) / std
    return z.clip(lower=-3.0, upper=3.0)


def _weighted_component_score(frame: pd.DataFrame, prefixes: tuple[str, ...], direction: str) -> pd.Series | None:
    available = [column for column in prefixes if column in frame.columns and frame[column].notna().any()]
    if not available:
        return None

    pieces: list[pd.Series] = []
    for column in available:
        values = frame[column]
        if direction == "short":
            values = -values
        pieces.append(_winsorized_z(values))

    if not pieces:
        return None
    return pd.concat(pieces, axis=1).mean(axis=1)


def rank_candidates(metrics_df: pd.DataFrame) -> pd.DataFrame:
    ranked = metrics_df.copy()

    ranked["momentum_component_long"] = _weighted_component_score(
        ranked,
        ("risk_adjusted_momentum_6m", "risk_adjusted_momentum_12m"),
        "long",
    )
    ranked["momentum_component_short"] = _weighted_component_score(
        ranked,
        ("risk_adjusted_momentum_6m", "risk_adjusted_momentum_12m"),
        "short",
    )

    rs_columns = tuple(
        column
        for column in ranked.columns
        if column.startswith("rs_") and (column.endswith("_6m_ex_1m") or column.endswith("_12m_ex_1m"))
    )
    ranked["relative_strength_component_long"] = _weighted_component_score(ranked, rs_columns, "long")
    ranked["relative_strength_component_short"] = _weighted_component_score(ranked, rs_columns, "short")

    ranked["trend_raw_long"] = (
        ranked["ema_fast_gap"].fillna(0.0) * 0.5
        + ranked["ema_slow_gap"].fillna(0.0) * 0.25
        + ranked["ema_stack_gap"].fillna(0.0) * 0.25
    )
    ranked["trend_raw_short"] = (
        -ranked["ema_fast_gap"].fillna(0.0) * 0.5
        - ranked["ema_slow_gap"].fillna(0.0) * 0.25
        - ranked["ema_stack_gap"].fillna(0.0) * 0.25
    )
    ranked["trend_quality_component_long"] = _winsorized_z(ranked["trend_raw_long"])
    ranked["trend_quality_component_short"] = _winsorized_z(ranked["trend_raw_short"])

    atr_fit_raw = ranked["atr_pct_14d"].apply(
        lambda value: _fit_score(
            value,
            float(DEFAULT_RULES["target_atr_pct"]),
            float(DEFAULT_RULES["atr_fit_band"]),
        )
    )
    ranked["atr_fit_component"] = _winsorized_z(atr_fit_raw)

    liquidity_raw = pd.Series(
        [
            math.log(value) if pd.notna(value) and value and value > 0 else math.nan
            for value in ranked["avg_dollar_volume_20d"]
        ],
        index=ranked.index,
    )
    liquidity_expansion = ranked["volume_expansion_20d_vs_60d"]
    ranked["liquidity_component"] = pd.concat(
        [
            _winsorized_z(liquidity_raw),
            _winsorized_z(liquidity_expansion),
        ],
        axis=1,
    ).mean(axis=1)

    ranked["quality_component_long"] = _weighted_component_score(
        ranked,
        ("roe_ttm", "debt_to_equity", "accruals_ratio"),
        "long",
    )
    ranked["quality_component_short"] = _weighted_component_score(
        ranked,
        ("roe_ttm", "debt_to_equity", "accruals_ratio"),
        "short",
    )
    ranked["earnings_revision_component_long"] = _weighted_component_score(ranked, ("earnings_revision_1m",), "long")
    ranked["earnings_revision_component_short"] = _weighted_component_score(ranked, ("earnings_revision_1m",), "short")
    ranked["short_crowding_component"] = _weighted_component_score(
        ranked,
        ("short_interest_pct_float", "days_to_cover"),
        "long",
    )

    def _score_row(row: pd.Series, side: str) -> float:
        score = 0.0
        weight_total = 0.0
        for component_name, weight in CORE_COMPONENT_WEIGHTS.items():
            if component_name in {"atr_fit", "liquidity"}:
                column = "atr_fit_component" if component_name == "atr_fit" else "liquidity_component"
            else:
                column = f"{component_name}_component_{side}"
            value = row.get(column)
            if pd.isna(value):
                continue
            score += float(value) * weight
            weight_total += weight

        for component_name, weight in OPTIONAL_COMPONENT_WEIGHTS.items():
            if component_name == "short_crowding_penalty":
                if side != "short":
                    continue
                value = row.get("short_crowding_component")
            else:
                value = row.get(f"{component_name}_component_{side}")
            if pd.isna(value):
                continue
            score += float(value) * weight
            weight_total += abs(weight)

        return 0.0 if weight_total == 0 else score / weight_total

    ranked["long_score"] = ranked.apply(lambda row: _score_row(row, "long"), axis=1)
    ranked["short_score"] = ranked.apply(lambda row: _score_row(row, "short"), axis=1)
    ranked["selection_side"] = ranked.apply(
        lambda row: "LONG" if row["long_score"] >= row["short_score"] else "SHORT",
        axis=1,
    )
    ranked["selection_score"] = ranked[["long_score", "short_score"]].max(axis=1)
    ranked.sort_values(
        by=["selection_score", "avg_dollar_volume_20d", "rank"],
        ascending=[False, False, True],
        inplace=True,
    )
    ranked.reset_index(drop=True, inplace=True)
    return ranked


def select_candidates(ranked_df: pd.DataFrame, top_k: int, long_count: int, short_count: int) -> pd.DataFrame:
    long_count = max(int(long_count), 0)
    short_count = max(int(short_count), 0)

    if long_count == 0 and short_count == 0:
        selected = ranked_df.sort_values(
            by=["selection_score", "avg_dollar_volume_20d", "rank"],
            ascending=[False, False, True],
        ).head(top_k)
        selected = selected.reset_index(drop=True)
        selected["selection_rank"] = selected.index + 1
        return selected

    longs = (
        ranked_df[ranked_df["selection_side"] == "LONG"]
        .sort_values(by=["long_score", "selection_score"], ascending=[False, False])
        .head(long_count)
    )
    shorts = (
        ranked_df[ranked_df["selection_side"] == "SHORT"]
        .sort_values(by=["short_score", "selection_score"], ascending=[False, False])
        .head(short_count)
    )

    selected = pd.concat([longs, shorts], axis=0).drop_duplicates(subset=["ticker"], keep="first")

    if len(selected) < top_k:
        remaining = ranked_df[~ranked_df["ticker"].isin(selected["ticker"])]
        selected = pd.concat([selected, remaining.head(top_k - len(selected))], axis=0)

    selected = (
        selected.sort_values(
            by=["selection_score", "avg_dollar_volume_20d", "rank"],
            ascending=[False, False, True],
        )
        .head(top_k)
        .reset_index(drop=True)
    )
    selected["selection_rank"] = selected.index + 1
    return selected


def optimize_universe(
    source_rows: list[dict[str, Any]],
    market_data: AlpacaClient,
    benchmark_symbols: list[str],
    batch_size: int,
    top_k: int,
    long_count: int,
    short_count: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    symbols = [row["ticker"] for row in source_rows]
    frames = _fetch_daily_frames(market_data, symbols, benchmark_symbols, batch_size)
    benchmark_frames = {symbol: frames.get(symbol) for symbol in benchmark_symbols}

    missing_benchmarks = [symbol for symbol, frame in benchmark_frames.items() if frame is None or frame.empty]
    if missing_benchmarks:
        raise ValueError(f"Missing benchmark bars for: {', '.join(missing_benchmarks)}")

    metric_rows: list[dict[str, Any]] = []
    for row in source_rows:
        frame = frames.get(row["ticker"])
        if frame is None or frame.empty:
            continue
        metrics = _extract_security_metrics(row, frame, benchmark_frames)
        if metrics is not None:
            metric_rows.append(metrics)

    if not metric_rows:
        raise ValueError("No eligible symbols remained after liquidity, history, and volatility filters.")

    metrics_df = pd.DataFrame(metric_rows)
    ranked_df = rank_candidates(metrics_df)
    selected_df = select_candidates(ranked_df, top_k=top_k, long_count=long_count, short_count=short_count)
    return ranked_df, selected_df


def _clean_yaml_value(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    return value


def _reason_tags(row: dict[str, Any], benchmark_symbols: list[str]) -> list[str]:
    tags: list[str] = []
    side = row.get("selection_side")
    if side == "LONG":
        if (row.get("momentum_6m_ex_1m") or 0) > 0 and (row.get("momentum_12m_ex_1m") or 0) > 0:
            tags.append("positive_6m_12m_momentum")
        if (row.get("ema_fast_gap") or 0) > 0 and (row.get("ema_slow_gap") or 0) > 0:
            tags.append("above_ema50_ema200")
    elif side == "SHORT":
        if (row.get("momentum_6m_ex_1m") or 0) < 0 and (row.get("momentum_12m_ex_1m") or 0) < 0:
            tags.append("negative_6m_12m_momentum")
        if (row.get("ema_fast_gap") or 0) < 0 and (row.get("ema_slow_gap") or 0) < 0:
            tags.append("below_ema50_ema200")

    if row.get("atr_pct_14d") is not None:
        tags.append("swing_range_volatility")
    if row.get("avg_dollar_volume_20d") is not None:
        tags.append("high_liquidity")

    rs_positive = 0
    rs_negative = 0
    for benchmark_symbol in benchmark_symbols:
        key_6m = f"rs_{benchmark_symbol.lower()}_6m_ex_1m"
        key_12m = f"rs_{benchmark_symbol.lower()}_12m_ex_1m"
        if (row.get(key_6m) or 0) > 0 and (row.get(key_12m) or 0) > 0:
            rs_positive += 1
        if (row.get(key_6m) or 0) < 0 and (row.get(key_12m) or 0) < 0:
            rs_negative += 1
    if rs_positive == len(benchmark_symbols) and rs_positive > 0:
        tags.append("outperforming_benchmarks")
    if rs_negative == len(benchmark_symbols) and rs_negative > 0:
        tags.append("underperforming_benchmarks")
    return tags


def build_output_payload(
    source_label: str,
    source_count: int,
    ranked_df: pd.DataFrame,
    selected_df: pd.DataFrame,
    benchmark_symbols: list[str],
) -> dict[str, Any]:
    selected_symbols: list[dict[str, Any]] = []
    for row in selected_df.to_dict(orient="records"):
        symbol_row = {
            "ticker": row["ticker"],
            "name": row.get("name"),
            "rank": int(row.get("rank", 0)),
            "country": "USA",
            "selection_rank": int(row["selection_rank"]),
            "selection_side": row["selection_side"],
            "selection_score": _clean_yaml_value(row["selection_score"]),
            "long_score": _clean_yaml_value(row["long_score"]),
            "short_score": _clean_yaml_value(row["short_score"]),
            "selection_reasons": _reason_tags(row, benchmark_symbols),
            "close": _clean_yaml_value(row["close"]),
            "avg_dollar_volume_20d": _clean_yaml_value(row["avg_dollar_volume_20d"]),
            "avg_dollar_volume_60d": _clean_yaml_value(row["avg_dollar_volume_60d"]),
            "volume_expansion_20d_vs_60d": _clean_yaml_value(row["volume_expansion_20d_vs_60d"]),
            "atr_14d": _clean_yaml_value(row["atr_14d"]),
            "atr_pct_14d": _clean_yaml_value(row["atr_pct_14d"]),
            "momentum_6m_ex_1m": _clean_yaml_value(row["momentum_6m_ex_1m"]),
            "momentum_12m_ex_1m": _clean_yaml_value(row["momentum_12m_ex_1m"]),
            "risk_adjusted_momentum_6m": _clean_yaml_value(row["risk_adjusted_momentum_6m"]),
            "risk_adjusted_momentum_12m": _clean_yaml_value(row["risk_adjusted_momentum_12m"]),
            "volatility_126d": _clean_yaml_value(row["volatility_126d"]),
            "ema_fast_gap": _clean_yaml_value(row["ema_fast_gap"]),
            "ema_slow_gap": _clean_yaml_value(row["ema_slow_gap"]),
            "ema_stack_gap": _clean_yaml_value(row["ema_stack_gap"]),
            "range_position_252d": _clean_yaml_value(row["range_position_252d"]),
            "component_scores": {
                "momentum_long": _clean_yaml_value(row.get("momentum_component_long")),
                "momentum_short": _clean_yaml_value(row.get("momentum_component_short")),
                "relative_strength_long": _clean_yaml_value(row.get("relative_strength_component_long")),
                "relative_strength_short": _clean_yaml_value(row.get("relative_strength_component_short")),
                "trend_quality_long": _clean_yaml_value(row.get("trend_quality_component_long")),
                "trend_quality_short": _clean_yaml_value(row.get("trend_quality_component_short")),
                "atr_fit": _clean_yaml_value(row.get("atr_fit_component")),
                "liquidity": _clean_yaml_value(row.get("liquidity_component")),
            },
        }
        for benchmark_symbol in benchmark_symbols:
            for suffix in ("6m_ex_1m", "12m_ex_1m"):
                key = f"rs_{benchmark_symbol.lower()}_{suffix}"
                if key in row:
                    symbol_row[key] = _clean_yaml_value(row[key])
        for optional_key in (
            "roe_ttm",
            "debt_to_equity",
            "accruals_ratio",
            "earnings_revision_1m",
            "short_interest_pct_float",
            "days_to_cover",
        ):
            if optional_key in row and row.get(optional_key) is not None:
                symbol_row[optional_key] = _clean_yaml_value(row.get(optional_key))
        selected_symbols.append(symbol_row)

    return {
        "metadata": {
            "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source_universe": source_label,
            "input_symbol_count": int(source_count),
            "eligible_symbol_count": int(len(ranked_df)),
            "selected_symbol_count": int(len(selected_df)),
            "benchmark_symbols": benchmark_symbols,
            "selection_profile": {
                "minimum_price": DEFAULT_RULES["minimum_price"],
                "minimum_avg_dollar_volume_20d": DEFAULT_RULES["minimum_avg_dollar_volume_20d"],
                "atr_pct_range": [DEFAULT_RULES["minimum_atr_pct"], DEFAULT_RULES["maximum_atr_pct"]],
                "momentum_formula": "0.5 * z(risk_adjusted_6m_ex_1m) + 0.5 * z(risk_adjusted_12m_ex_1m)",
                "relative_strength_formula": "average z-score of stock return minus SPY/QQQ over 6m_ex_1m and 12m_ex_1m",
                "trend_formula": "close-vs-EMA50, close-vs-EMA200, and EMA50-vs-EMA200 stack",
                "atr_fit_formula": "score highest near target ATR% and lower outside the preferred swing-trading band",
                "liquidity_formula": "blend of 20d average dollar volume and 20d/60d participation expansion",
                "optional_fields": [
                    "roe_ttm",
                    "debt_to_equity",
                    "accruals_ratio",
                    "earnings_revision_1m",
                    "short_interest_pct_float",
                    "days_to_cover",
                ],
            },
            "component_weights": {
                "core": CORE_COMPONENT_WEIGHTS,
                "optional": OPTIONAL_COMPONENT_WEIGHTS,
            },
        },
        "symbols": selected_symbols,
    }


def write_output_yaml(path: str | Path, payload: dict[str, Any]) -> Path:
    output_path = Path(path)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    source = load_symbol_source(settings, args.input_file, args.input_url, args.input_format)
    benchmark_symbols = [_normalize_symbol(item) for item in args.benchmarks.split(",") if _normalize_symbol(item)]
    if not benchmark_symbols:
        raise ValueError("At least one benchmark symbol is required.")

    market_data = AlpacaClient(settings.alpaca, storage=None, market_timezone=settings.app.timezone)
    ranked_df, selected_df = optimize_universe(
        source_rows=source.rows,
        market_data=market_data,
        benchmark_symbols=benchmark_symbols,
        batch_size=args.batch_size,
        top_k=args.top_k,
        long_count=args.long_count,
        short_count=args.short_count,
    )

    payload = build_output_payload(source.label, len(source.rows), ranked_df, selected_df, benchmark_symbols)
    output_path = write_output_yaml(args.output_file, payload)

    long_selected = int((selected_df["selection_side"] == "LONG").sum())
    short_selected = int((selected_df["selection_side"] == "SHORT").sum())
    logger.info(
        "optimized %s input symbols into %s selections (%s long / %s short): %s",
        len(source.rows),
        len(selected_df),
        long_selected,
        short_selected,
        output_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
