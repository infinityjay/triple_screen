# src/data_fetcher.py
# yfinance 数据获取封装（免费，无需 API Key）

import yfinance as yf
import pandas as pd


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名、清理 MultiIndex"""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    # 去掉时区信息，只保留日期
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.dropna(subset=["close"])


def fetch_history(ticker: str, period: str = "10y") -> pd.DataFrame:
    """首次初始化：下载过去N年日线数据"""
    df = yf.download(ticker, period=period, interval="1d",
                     auto_adjust=True, progress=False)
    return _clean(df)


def fetch_weekly(ticker: str, period: str = "10y") -> pd.DataFrame:
    """周线数据，用于第一重过滤"""
    df = yf.download(ticker, period=period, interval="1wk",
                     auto_adjust=True, progress=False)
    return _clean(df)


def fetch_latest(ticker: str, days: int = 10) -> pd.DataFrame:
    """每日增量：只拉最近N天（覆盖写入，确保最新收盘价）"""
    df = yf.download(ticker, period=f"{days}d", interval="1d",
                     auto_adjust=True, progress=False)
    return _clean(df)


def fetch_latest_weekly(ticker: str, weeks: int = 5) -> pd.DataFrame:
    """每日增量：更新最近几周的周线"""
    df = yf.download(ticker, period=f"{weeks * 7}d", interval="1wk",
                     auto_adjust=True, progress=False)
    return _clean(df)
