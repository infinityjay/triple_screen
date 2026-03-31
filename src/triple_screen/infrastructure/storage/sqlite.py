from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class SQLiteStorage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol TEXT PRIMARY KEY,
                    market_cap REAL,
                    sector TEXT,
                    updated_at TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS weekly_indicators (
                    symbol TEXT PRIMARY KEY,
                    macd REAL,
                    macd_signal REAL,
                    histogram REAL,
                    histogram_prev REAL,
                    confirmed_bars INTEGER,
                    trend TEXT,
                    updated_at TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_indicators (
                    symbol TEXT PRIMARY KEY,
                    rsi REAL,
                    rsi_prev REAL,
                    rsi_state TEXT,
                    updated_at TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS hourly_indicators (
                    symbol TEXT PRIMARY KEY,
                    close REAL,
                    high_n REAL,
                    low_n REAL,
                    atr REAL,
                    breakout_long INTEGER,
                    breakout_short INTEGER,
                    updated_at TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    direction TEXT,
                    entry_price REAL,
                    stop_loss_atr REAL,
                    stop_loss_prev_candle REAL,
                    take_profit_rr REAL,
                    signal_score REAL,
                    weekly_histogram REAL,
                    weekly_trend TEXT,
                    daily_rsi REAL,
                    hourly_close REAL,
                    hourly_atr REAL,
                    position_size REAL,
                    created_at TEXT,
                    alerted INTEGER DEFAULT 0
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_log (
                    symbol TEXT PRIMARY KEY,
                    last_alert_at TEXT,
                    last_direction TEXT
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at)")

    def upsert_symbol(self, symbol: str, market_cap: float | None, sector: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO symbols (symbol, market_cap, sector, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (symbol, market_cap, sector, datetime.utcnow().isoformat()),
            )

    def upsert_weekly(
        self,
        symbol: str,
        macd: float,
        macd_signal: float,
        histogram: float,
        histogram_prev: float,
        confirmed_bars: int,
        trend: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO weekly_indicators
                (symbol, macd, macd_signal, histogram, histogram_prev, confirmed_bars, trend, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    macd,
                    macd_signal,
                    histogram,
                    histogram_prev,
                    confirmed_bars,
                    trend,
                    datetime.utcnow().isoformat(),
                ),
            )

    def upsert_daily(self, symbol: str, rsi: float, rsi_prev: float, rsi_state: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO daily_indicators
                (symbol, rsi, rsi_prev, rsi_state, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (symbol, rsi, rsi_prev, rsi_state, datetime.utcnow().isoformat()),
            )

    def upsert_hourly(
        self,
        symbol: str,
        close: float,
        high_n: float,
        low_n: float,
        atr: float,
        breakout_long: bool,
        breakout_short: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO hourly_indicators
                (symbol, close, high_n, low_n, atr, breakout_long, breakout_short, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    close,
                    high_n,
                    low_n,
                    atr,
                    int(breakout_long),
                    int(breakout_short),
                    datetime.utcnow().isoformat(),
                ),
            )

    def save_signal(
        self,
        symbol: str,
        direction: str,
        entry: float,
        sl_atr: float,
        sl_candle: float,
        tp_rr: float,
        score: float,
        w_hist: float,
        w_trend: str,
        d_rsi: float,
        h_close: float,
        h_atr: float,
        pos_size: float,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO signals
                (symbol, direction, entry_price, stop_loss_atr, stop_loss_prev_candle,
                 take_profit_rr, signal_score, weekly_histogram, weekly_trend, daily_rsi,
                 hourly_close, hourly_atr, position_size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    direction,
                    entry,
                    sl_atr,
                    sl_candle,
                    tp_rr,
                    score,
                    w_hist,
                    w_trend,
                    d_rsi,
                    h_close,
                    h_atr,
                    pos_size,
                    datetime.utcnow().isoformat(),
                ),
            )

    def get_last_alert(self, symbol: str):
        with self._connect() as connection:
            return connection.execute(
                "SELECT last_alert_at, last_direction FROM alert_log WHERE symbol = ?",
                (symbol,),
            ).fetchone()

    def update_alert_log(self, symbol: str, direction: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO alert_log (symbol, last_alert_at, last_direction)
                VALUES (?, ?, ?)
                """,
                (symbol, datetime.utcnow().isoformat(), direction),
            )
