# src/storage.py
# SQLite 本地存储：首次初始化历史数据，每日增量追加

import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "market.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            ticker   TEXT    NOT NULL,
            date     TEXT    NOT NULL,
            open     REAL,
            high     REAL,
            low      REAL,
            close    REAL,
            volume   INTEGER,
            interval TEXT    NOT NULL DEFAULT 'daily',
            PRIMARY KEY (ticker, date, interval)
        )
    """)
    conn.commit()
    conn.close()


def save_df(ticker: str, df: pd.DataFrame, interval: str = "daily"):
    """保存 DataFrame 到数据库，重复数据自动覆盖（REPLACE）"""
    if df.empty:
        return
    df = df.copy()
    df.index = df.index.strftime("%Y-%m-%d")
    df["ticker"] = ticker
    df["interval"] = interval

    needed = ["open", "high", "low", "close", "volume"]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    rows = df.reset_index().rename(columns={"index": "date", "date": "date"})
    rows = rows[["ticker", "date", "open", "high", "low", "close", "volume", "interval"]]

    conn = sqlite3.connect(DB_PATH)
    # INSERT OR REPLACE 确保最新数据覆盖旧数据
    conn.executemany(
        "INSERT OR REPLACE INTO ohlcv "
        "(ticker, date, open, high, low, close, volume, interval) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows.itertuples(index=False, name=None)
    )
    conn.commit()
    conn.close()


def load_df(ticker: str, interval: str = "daily") -> pd.DataFrame:
    """读取指定 ticker 的全部数据，按日期升序"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT * FROM ohlcv WHERE ticker=? AND interval=? ORDER BY date",
        conn, params=(ticker, interval)
    )
    conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df


def ticker_exists(ticker: str) -> bool:
    """检查某个 ticker 是否已有历史数据"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT COUNT(*) FROM ohlcv WHERE ticker=? AND interval='daily'",
        (ticker,)
    ).fetchone()
    conn.close()
    return row[0] > 100  # 超过100条才算真正初始化过


def get_all_tickers() -> list:
    """返回数据库中已有数据的所有 ticker"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM ohlcv WHERE interval='daily'"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]
