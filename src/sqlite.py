from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd


class SQLiteStorage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _json_safe(value):
        if isinstance(value, dict):
            return {str(key): SQLiteStorage._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [SQLiteStorage._json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [SQLiteStorage._json_safe(item) for item in value]
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if hasattr(value, "item"):
            try:
                return SQLiteStorage._json_safe(value.item())
            except Exception:
                pass
        return value

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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS price_bars (
                    symbol TEXT,
                    timeframe TEXT,
                    timestamp TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    updated_at TEXT,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS earnings_events (
                    symbol TEXT PRIMARY KEY,
                    report_date TEXT,
                    fiscal_date_ending TEXT,
                    estimate TEXT,
                    updated_at TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS qualified_candidates (
                    session_date TEXT,
                    symbol TEXT,
                    direction TEXT,
                    signal_score REAL,
                    reward_risk_ratio REAL,
                    opportunity_status TEXT,
                    strong_divergence INTEGER,
                    candidate_json TEXT,
                    created_at TEXT,
                    PRIMARY KEY (session_date, symbol, direction)
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_bars_lookup ON price_bars(symbol, timeframe, timestamp)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_session_score ON qualified_candidates(session_date, signal_score DESC)"
            )

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
        sl_safezone: float,
        sl_two_bar: float,
        take_profit: float,
        score: float,
        w_hist: float,
        w_trend: str,
        d_rsi: float,
        h_close: float,
        h_atr: float,
        pos_size: float | None,
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
                    sl_safezone,
                    sl_two_bar,
                    take_profit,
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

    def upsert_earnings_events(self, events: list[dict]) -> None:
        if not events:
            return

        now = datetime.utcnow().isoformat()
        records = [
            (
                item["symbol"],
                item.get("report_date"),
                item.get("fiscal_date_ending"),
                item.get("estimate"),
                now,
            )
            for item in events
            if item.get("symbol")
        ]
        if not records:
            return

        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO earnings_events
                (symbol, report_date, fiscal_date_ending, estimate, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                records,
            )

    def get_earnings_event(self, symbol: str):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT symbol, report_date, fiscal_date_ending, estimate, updated_at
                FROM earnings_events
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()

    def get_latest_earnings_update_time(self) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT updated_at
                FROM earnings_events
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row["updated_at"])

    def replace_qualified_candidates(self, session_date: str, candidates: list[dict]) -> None:
        now = datetime.utcnow().isoformat()
        records = [
            (
                session_date,
                item["symbol"],
                item["direction"],
                item["signal_score"],
                item["exits"]["reward_risk_ratio"],
                item["opportunity_status"],
                int(bool(item.get("strong_divergence"))),
                json.dumps(self._json_safe(item), ensure_ascii=True),
                now,
            )
            for item in candidates
        ]

        with self._connect() as connection:
            connection.execute("DELETE FROM qualified_candidates WHERE session_date = ?", (session_date,))
            if records:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO qualified_candidates
                    (session_date, symbol, direction, signal_score, reward_risk_ratio,
                     opportunity_status, strong_divergence, candidate_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    records,
                )
            connection.execute(
                """
                DELETE FROM qualified_candidates
                WHERE session_date NOT IN (
                    SELECT session_date
                    FROM qualified_candidates
                    GROUP BY session_date
                    ORDER BY session_date DESC
                    LIMIT 5
                )
                """
            )

    def get_latest_candidate_session(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_date
                FROM qualified_candidates
                GROUP BY session_date
                ORDER BY session_date DESC
                LIMIT 1
                """
            ).fetchone()
        return row["session_date"] if row else None

    def get_qualified_candidates(self, session_date: str | None = None) -> list[dict]:
        target_session = session_date or self.get_latest_candidate_session()
        if not target_session:
            return []

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT candidate_json
                FROM qualified_candidates
                WHERE session_date = ?
                ORDER BY signal_score DESC, symbol ASC
                """,
                (target_session,),
            ).fetchall()

        return [json.loads(row["candidate_json"]) for row in rows]

    def get_recent_qualified_candidates(self, session_limit: int = 5) -> list[dict]:
        capped_limit = max(int(session_limit), 1)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_date, candidate_json
                FROM qualified_candidates
                WHERE session_date IN (
                    SELECT session_date
                    FROM qualified_candidates
                    GROUP BY session_date
                    ORDER BY session_date DESC
                    LIMIT ?
                )
                ORDER BY session_date DESC, signal_score DESC, symbol ASC
                """,
                (capped_limit,),
            ).fetchall()

        items: list[dict] = []
        for row in rows:
            payload = json.loads(row["candidate_json"])
            payload["stored_session_date"] = row["session_date"]
            items.append(payload)
        return items

    def get_price_bars(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM price_bars
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp ASC
                """,
                (symbol, timeframe),
            ).fetchall()

        if not rows:
            return None

        frame = pd.DataFrame([dict(row) for row in rows])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        frame.set_index("timestamp", inplace=True)
        return frame

    def get_latest_bar_timestamp(self, symbol: str, timeframe: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT timestamp
                FROM price_bars
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (symbol, timeframe),
            ).fetchone()

        if not row:
            return None
        return datetime.fromisoformat(row["timestamp"])

    def get_latest_bar_sync_time(self, symbol: str, timeframe: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT updated_at
                FROM price_bars
                WHERE symbol = ? AND timeframe = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (symbol, timeframe),
            ).fetchone()

        if not row:
            return None
        return datetime.fromisoformat(row["updated_at"])

    def upsert_price_bars(self, symbol: str, timeframe: str, frame: pd.DataFrame) -> None:
        if frame is None or frame.empty:
            return

        records = []
        now = datetime.utcnow().isoformat()
        for timestamp, row in frame.iterrows():
            records.append(
                (
                    symbol,
                    timeframe,
                    pd.Timestamp(timestamp).to_pydatetime().isoformat(),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row.get("volume", 0.0)),
                    now,
                )
            )

        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO price_bars
                (symbol, timeframe, timestamp, open, high, low, close, volume, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )

    def trim_price_bars(self, symbol: str, timeframe: str, keep_rows: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM price_bars
                WHERE symbol = ?
                  AND timeframe = ?
                  AND timestamp NOT IN (
                      SELECT timestamp
                      FROM price_bars
                      WHERE symbol = ? AND timeframe = ?
                      ORDER BY timestamp DESC
                      LIMIT ?
                  )
                """,
                (symbol, timeframe, symbol, timeframe, keep_rows),
            )
