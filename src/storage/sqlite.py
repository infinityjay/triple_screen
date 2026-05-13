from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

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
    def _row_to_dict(row):
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)

    @classmethod
    def _utc_iso(cls) -> str:
        return cls._utc_now().isoformat()

    @classmethod
    def _utc_year_month(cls) -> str:
        return cls._utc_now().strftime("%Y-%m")

    @staticmethod
    def _parse_db_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {row["name"] for row in rows}
        if column_name in existing_columns:
            return
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

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
                CREATE TABLE IF NOT EXISTS divergence_alert_log (
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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS planned_orders (
                    id TEXT PRIMARY KEY,
                    session_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    broker TEXT DEFAULT 'IBKR',
                    broker_order_id TEXT,
                    order_type TEXT,
                    action TEXT,
                    quantity REAL,
                    stop_price REAL,
                    limit_price REAL,
                    status TEXT,
                    submitted_at TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    stock TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT 'long',
                    buy_price REAL,
                    shares REAL,
                    stop_loss REAL,
                    initial_stop_loss REAL,
                    initial_stop_basis TEXT,
                    stop_reason TEXT,
                    buy_date TEXT,
                    day_high REAL,
                    day_low REAL,
                    target_price REAL,
                    target_pct REAL,
                    chan_high REAL,
                    chan_low REAL,
                    sell_price REAL,
                    sell_date TEXT,
                    sell_high REAL,
                    sell_low REAL,
                    sell_reason TEXT,
                    buy_comm REAL DEFAULT 1,
                    sell_comm REAL DEFAULT 0,
                    review TEXT,
                    used_stop REAL,
                    pnl REAL,
                    pnl_net REAL,
                    protective_stop_basis TEXT,
                    stop_updated_at TEXT,
                    last_stop_session_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_settings (
                    id INTEGER PRIMARY KEY,
                    total REAL NOT NULL DEFAULT 0,
                    single_stop REAL NOT NULL DEFAULT 2,
                    month_stop REAL NOT NULL DEFAULT 6,
                    report_month TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_stop_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT,
                    symbol TEXT,
                    direction TEXT,
                    session_date TEXT,
                    previous_stop_loss REAL,
                    proposed_stop_loss REAL,
                    applied_stop_loss REAL,
                    stop_basis TEXT,
                    changed INTEGER DEFAULT 0,
                    status TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id TEXT PRIMARY KEY,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    initial_capital REAL NOT NULL,
                    risk_pct REAL NOT NULL,
                    max_total_open_risk_pct REAL NOT NULL,
                    max_open_positions INTEGER NOT NULL,
                    assumptions_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_trades (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_timestamp TEXT NOT NULL,
                    exit_timestamp TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    initial_stop REAL NOT NULL,
                    final_stop REAL NOT NULL,
                    shares REAL NOT NULL,
                    pnl REAL NOT NULL,
                    pnl_pct REAL NOT NULL,
                    r_multiple REAL NOT NULL,
                    exit_reason TEXT NOT NULL,
                    position_cost REAL,
                    entry_cash_before REAL,
                    entry_equity_before REAL,
                    entry_open_risk_before REAL,
                    entry_remaining_stop_budget REAL,
                    entry_allowed_risk REAL,
                    trade_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_bars_lookup ON price_bars(symbol, timeframe, timestamp)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_session_score ON qualified_candidates(session_date, signal_score DESC)"
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_planned_orders_session ON planned_orders(session_date, symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at DESC)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_trade_stop_updates_session ON trade_stop_updates(session_date, created_at DESC)"
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_backtest_runs_created_at ON backtest_runs(created_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_backtest_trades_run_id ON backtest_trades(run_id, sequence)")
            self._ensure_column(cursor.connection, "trades", "initial_stop_loss", "REAL")
            self._ensure_column(cursor.connection, "trades", "initial_stop_basis", "TEXT")
            self._ensure_column(cursor.connection, "planned_orders", "stop_loss", "REAL")
            self._ensure_column(cursor.connection, "trade_stop_updates", "proposed_stop_hourly_safezone", "REAL")
            cursor.execute(
                """
                UPDATE trades
                SET initial_stop_loss = COALESCE(initial_stop_loss, stop_loss),
                    initial_stop_basis = COALESCE(initial_stop_basis, protective_stop_basis, 'UNKNOWN')
                WHERE initial_stop_loss IS NULL OR initial_stop_basis IS NULL
                """
            )
            cursor.execute(
                """
                INSERT OR IGNORE INTO trade_settings (id, total, single_stop, month_stop, report_month, updated_at)
                VALUES (1, 0, 2, 6, ?, ?)
                """,
                (self._utc_year_month(), self._utc_iso()),
            )

    def upsert_symbol(self, symbol: str, market_cap: float | None, sector: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO symbols (symbol, market_cap, sector, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (symbol, market_cap, sector, self._utc_iso()),
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
                    self._utc_iso(),
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
                (symbol, rsi, rsi_prev, rsi_state, self._utc_iso()),
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
                    self._utc_iso(),
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
                    self._utc_iso(),
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
                (symbol, self._utc_iso(), direction),
            )

    def get_last_divergence_alert(self, symbol: str):
        with self._connect() as connection:
            return connection.execute(
                "SELECT last_alert_at, last_direction FROM divergence_alert_log WHERE symbol = ?",
                (symbol,),
            ).fetchone()

    def update_divergence_alert_log(self, symbol: str, direction: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO divergence_alert_log (symbol, last_alert_at, last_direction)
                VALUES (?, ?, ?)
                """,
                (symbol, self._utc_iso(), direction),
            )

    def upsert_earnings_events(self, events: list[dict]) -> None:
        if not events:
            return

        now = self._utc_iso()
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
        return self._parse_db_datetime(row["updated_at"])

    def replace_qualified_candidates(self, session_date: str, candidates: list[dict]) -> None:
        now = self._utc_iso()
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

        items = [json.loads(row["candidate_json"]) for row in rows]

        def sort_key(item: dict) -> tuple[float, str]:
            score = item.get("candidate_rank_score", item.get("candidate_score", item.get("signal_score", 0.0)))
            return (-float(score or 0.0), item.get("symbol", ""))

        return sorted(items, key=sort_key)

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

    def list_candidate_sessions(self, limit: int = 10) -> list[dict]:
        capped_limit = max(int(limit), 1)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    session_date,
                    COUNT(*) AS candidate_count,
                    SUM(CASE WHEN opportunity_status = 'TRIGGERED' THEN 1 ELSE 0 END) AS triggered_count,
                    SUM(CASE WHEN direction = 'LONG' THEN 1 ELSE 0 END) AS long_count,
                    SUM(CASE WHEN direction = 'SHORT' THEN 1 ELSE 0 END) AS short_count,
                    SUM(CASE WHEN strong_divergence = 1 THEN 1 ELSE 0 END) AS strong_divergence_count,
                    MAX(created_at) AS updated_at
                FROM qualified_candidates
                GROUP BY session_date
                ORDER BY session_date DESC
                LIMIT ?
                """,
                (capped_limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_planned_orders(self, session_date: str | None = None) -> list[dict]:
        query = """
            SELECT id, session_date, symbol, direction, broker, broker_order_id, order_type, action,
                   quantity, stop_price, limit_price, stop_loss, status, submitted_at, notes, created_at, updated_at
            FROM planned_orders
        """
        params: tuple = ()
        if session_date:
            query += " WHERE session_date = ?"
            params = (session_date,)
        query += " ORDER BY session_date DESC, symbol ASC, created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def upsert_planned_order(self, payload: dict) -> dict:
        now = self._utc_iso()
        order_id = str(payload.get("id") or uuid4())
        record = {
            "id": order_id,
            "session_date": str(payload.get("session_date") or ""),
            "symbol": str(payload.get("symbol") or "").strip().upper(),
            "direction": str(payload.get("direction") or "").strip().upper(),
            "broker": str(payload.get("broker") or "IBKR").strip().upper(),
            "broker_order_id": payload.get("broker_order_id"),
            "order_type": payload.get("order_type"),
            "action": payload.get("action"),
            "quantity": payload.get("quantity"),
            "stop_price": payload.get("stop_price"),
            "limit_price": payload.get("limit_price"),
            "stop_loss": payload.get("stop_loss"),
            "status": str(payload.get("status") or "SUBMITTED").strip().upper(),
            "submitted_at": payload.get("submitted_at"),
            "notes": payload.get("notes"),
            "created_at": payload.get("created_at") or now,
            "updated_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO planned_orders
                (id, session_date, symbol, direction, broker, broker_order_id, order_type, action,
                 quantity, stop_price, limit_price, stop_loss, status, submitted_at, notes, created_at, updated_at)
                VALUES (:id, :session_date, :symbol, :direction, :broker, :broker_order_id, :order_type, :action,
                        :quantity, :stop_price, :limit_price, :stop_loss, :status, :submitted_at, :notes, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    session_date = excluded.session_date,
                    symbol = excluded.symbol,
                    direction = excluded.direction,
                    broker = excluded.broker,
                    broker_order_id = excluded.broker_order_id,
                    order_type = excluded.order_type,
                    action = excluded.action,
                    quantity = excluded.quantity,
                    stop_price = excluded.stop_price,
                    limit_price = excluded.limit_price,
                    stop_loss = excluded.stop_loss,
                    status = excluded.status,
                    submitted_at = excluded.submitted_at,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                record,
            )
        return record

    def delete_planned_order(self, order_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM planned_orders WHERE id = ?", (order_id,))
        return cursor.rowcount > 0

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
        return self._parse_db_datetime(row["timestamp"])

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
        return self._parse_db_datetime(row["updated_at"])

    def upsert_price_bars(self, symbol: str, timeframe: str, frame: pd.DataFrame) -> None:
        if frame is None or frame.empty:
            return

        records = []
        now = self._utc_iso()
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

    @staticmethod
    def _trade_select_clause() -> str:
        return """
            SELECT
                t.*,
                su.applied_stop_loss AS suggested_stop_loss,
                su.proposed_stop_loss AS suggested_stop_candidate,
                su.proposed_stop_hourly_safezone AS suggested_stop_hourly_safezone,
                su.stop_basis AS suggested_stop_basis,
                su.session_date AS suggested_stop_session_date,
                su.created_at AS suggested_stop_updated_at
            FROM trades t
            LEFT JOIN (
                SELECT
                    u.trade_id,
                    u.proposed_stop_loss,
                    u.proposed_stop_hourly_safezone,
                    u.applied_stop_loss,
                    u.stop_basis,
                    u.session_date,
                    u.created_at
                FROM trade_stop_updates u
                INNER JOIN (
                    SELECT trade_id, MAX(id) AS max_id
                    FROM trade_stop_updates
                    GROUP BY trade_id
                ) latest
                    ON latest.trade_id = u.trade_id
                   AND latest.max_id = u.id
            ) su
                ON su.trade_id = t.id
        """

    def list_trades(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                {self._trade_select_clause()}
                ORDER BY datetime(t.created_at) DESC, datetime(COALESCE(t.buy_date, t.created_at)) DESC, t.stock ASC
                """
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_open_trades(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                {self._trade_select_clause()}
                WHERE t.sell_price IS NULL OR t.sell_date IS NULL OR TRIM(COALESCE(t.sell_date, '')) = ''
                ORDER BY datetime(COALESCE(t.buy_date, t.created_at)) DESC, t.stock ASC
                """
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_trade(self, trade_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                {self._trade_select_clause()}
                WHERE t.id = ?
                """,
                (trade_id,),
            ).fetchone()
        return self._row_to_dict(row)

    def insert_trade(self, payload: dict) -> dict:
        trade_id = str(payload.get("id") or uuid4().hex)
        now = self._utc_iso()
        row = {
            "id": trade_id,
            "stock": str(payload.get("stock", "")).strip().upper(),
            "direction": str(payload.get("direction", "long")).strip().lower() or "long",
            "buy_price": payload.get("buy_price"),
            "shares": payload.get("shares"),
            "stop_loss": payload.get("stop_loss"),
            "initial_stop_loss": (
                payload.get("initial_stop_loss") if payload.get("initial_stop_loss") is not None else payload.get("stop_loss")
            ),
            "initial_stop_basis": payload.get("initial_stop_basis")
            or payload.get("protective_stop_basis")
            or ("MANUAL" if payload.get("stop_loss") is not None else None),
            "stop_reason": payload.get("stop_reason"),
            "buy_date": payload.get("buy_date"),
            "day_high": payload.get("day_high"),
            "day_low": payload.get("day_low"),
            "target_price": payload.get("target_price"),
            "target_pct": payload.get("target_pct"),
            "chan_high": payload.get("chan_high"),
            "chan_low": payload.get("chan_low"),
            "sell_price": payload.get("sell_price"),
            "sell_date": payload.get("sell_date"),
            "sell_high": payload.get("sell_high"),
            "sell_low": payload.get("sell_low"),
            "sell_reason": payload.get("sell_reason"),
            "buy_comm": payload.get("buy_comm"),
            "sell_comm": payload.get("sell_comm"),
            "review": payload.get("review"),
            "used_stop": payload.get("used_stop"),
            "pnl": payload.get("pnl"),
            "pnl_net": payload.get("pnl_net"),
            "protective_stop_basis": payload.get("protective_stop_basis")
            or payload.get("initial_stop_basis")
            or ("MANUAL" if payload.get("stop_loss") is not None else None),
            "stop_updated_at": payload.get("stop_updated_at"),
            "last_stop_session_date": payload.get("last_stop_session_date"),
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trades (
                    id, stock, direction, buy_price, shares, stop_loss, initial_stop_loss, initial_stop_basis, stop_reason, buy_date,
                    day_high, day_low, target_price, target_pct, chan_high, chan_low,
                    sell_price, sell_date, sell_high, sell_low, sell_reason,
                    buy_comm, sell_comm, review, used_stop, pnl, pnl_net,
                    protective_stop_basis, stop_updated_at, last_stop_session_date,
                    created_at, updated_at
                )
                VALUES (
                    :id, :stock, :direction, :buy_price, :shares, :stop_loss, :initial_stop_loss, :initial_stop_basis, :stop_reason, :buy_date,
                    :day_high, :day_low, :target_price, :target_pct, :chan_high, :chan_low,
                    :sell_price, :sell_date, :sell_high, :sell_low, :sell_reason,
                    :buy_comm, :sell_comm, :review, :used_stop, :pnl, :pnl_net,
                    :protective_stop_basis, :stop_updated_at, :last_stop_session_date,
                    :created_at, :updated_at
                )
                """,
                row,
            )
        return self.get_trade(trade_id) or row

    def update_trade(self, trade_id: str, payload: dict) -> dict | None:
        current = self.get_trade(trade_id)
        if not current:
            return None

        merged = dict(current)
        for key, value in payload.items():
            if key in merged and key not in {"id", "created_at"}:
                if key == "stock" and value is not None:
                    merged[key] = str(value).strip().upper()
                elif key == "direction" and value is not None:
                    merged[key] = str(value).strip().lower() or "long"
                else:
                    merged[key] = value
        if payload.get("initial_stop_loss") is not None:
            merged["initial_stop_loss"] = payload.get("initial_stop_loss")
        elif payload.get("stop_loss") is not None and (merged.get("initial_stop_loss") is None or not merged.get("stop_updated_at")):
            merged["initial_stop_loss"] = payload.get("stop_loss")

        if payload.get("initial_stop_basis") is not None:
            merged["initial_stop_basis"] = payload.get("initial_stop_basis")
        elif payload.get("stop_loss") is not None and (merged.get("initial_stop_basis") is None or not merged.get("stop_updated_at")):
            merged["initial_stop_basis"] = payload.get("protective_stop_basis") or "MANUAL"
        if payload.get("protective_stop_basis") is not None:
            merged["protective_stop_basis"] = payload.get("protective_stop_basis")
        elif payload.get("stop_loss") is not None and not merged.get("stop_updated_at"):
            merged["protective_stop_basis"] = merged.get("initial_stop_basis") or "MANUAL"
        merged["updated_at"] = self._utc_iso()

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE trades
                SET stock = :stock,
                    direction = :direction,
                    buy_price = :buy_price,
                    shares = :shares,
                    stop_loss = :stop_loss,
                    initial_stop_loss = :initial_stop_loss,
                    initial_stop_basis = :initial_stop_basis,
                    stop_reason = :stop_reason,
                    buy_date = :buy_date,
                    day_high = :day_high,
                    day_low = :day_low,
                    target_price = :target_price,
                    target_pct = :target_pct,
                    chan_high = :chan_high,
                    chan_low = :chan_low,
                    sell_price = :sell_price,
                    sell_date = :sell_date,
                    sell_high = :sell_high,
                    sell_low = :sell_low,
                    sell_reason = :sell_reason,
                    buy_comm = :buy_comm,
                    sell_comm = :sell_comm,
                    review = :review,
                    used_stop = :used_stop,
                    pnl = :pnl,
                    pnl_net = :pnl_net,
                    protective_stop_basis = :protective_stop_basis,
                    stop_updated_at = :stop_updated_at,
                    last_stop_session_date = :last_stop_session_date,
                    updated_at = :updated_at
                WHERE id = :id
                """,
                merged,
            )
        return self.get_trade(trade_id)

    def delete_trade(self, trade_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        return cursor.rowcount > 0

    def clear_trades(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM trades")
        return int(cursor.rowcount or 0)

    def get_trade_settings(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, total, single_stop, month_stop, report_month, updated_at
                FROM trade_settings
                WHERE id = 1
                """
            ).fetchone()
        if row:
            return self._row_to_dict(row)
        return {
            "id": 1,
            "total": 0,
            "single_stop": 2,
            "month_stop": 6,
            "report_month": self._utc_year_month(),
            "updated_at": self._utc_iso(),
        }

    def upsert_trade_settings(self, payload: dict) -> dict:
        row = {
            "id": int(payload.get("id", 1)),
            "total": payload.get("total", 0),
            "single_stop": payload.get("single_stop", 2),
            "month_stop": payload.get("month_stop", 6),
            "report_month": payload.get("report_month") or self._utc_year_month(),
            "updated_at": self._utc_iso(),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO trade_settings (id, total, single_stop, month_stop, report_month, updated_at)
                VALUES (:id, :total, :single_stop, :month_stop, :report_month, :updated_at)
                """,
                row,
            )
        return self.get_trade_settings()

    def update_trade_protective_stop(
        self,
        trade_id: str,
        stop_loss: float | None,
        used_stop: float | None,
        stop_basis: str,
        session_date: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE trades
                SET protective_stop_basis = ?,
                    stop_updated_at = ?,
                    last_stop_session_date = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    stop_basis,
                    self._utc_iso(),
                    session_date,
                    self._utc_iso(),
                    trade_id,
                ),
            )

    def insert_trade_stop_updates(self, updates: list[dict]) -> None:
        if not updates:
            return
        records = [
            (
                item.get("trade_id"),
                item.get("symbol"),
                item.get("direction"),
                item.get("session_date"),
                item.get("previous_stop_loss"),
                item.get("proposed_stop_loss"),
                item.get("proposed_stop_hourly_safezone"),
                item.get("applied_stop_loss"),
                item.get("stop_basis"),
                int(bool(item.get("changed"))),
                item.get("status"),
                item.get("note"),
                self._utc_iso(),
            )
            for item in updates
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO trade_stop_updates (
                    trade_id, symbol, direction, session_date, previous_stop_loss,
                    proposed_stop_loss, proposed_stop_hourly_safezone, applied_stop_loss, stop_basis, changed,
                    status, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )

    def insert_backtest_run(
        self,
        payload: dict,
        trades: list[dict],
    ) -> str:
        run_id = str(payload.get("id") or uuid4().hex)
        created_at = self._utc_iso()
        row = {
            "id": run_id,
            "start_date": str(payload["start_date"]),
            "end_date": str(payload["end_date"]),
            "initial_capital": float(payload["initial_capital"]),
            "risk_pct": float(payload["risk_pct"]),
            "max_total_open_risk_pct": float(payload["max_total_open_risk_pct"]),
            "max_open_positions": int(payload["max_open_positions"]),
            "assumptions_json": json.dumps(self._json_safe(payload.get("assumptions", {})), ensure_ascii=True),
            "summary_json": json.dumps(self._json_safe(payload.get("summary", {})), ensure_ascii=True),
            "created_at": created_at,
        }

        trade_records = []
        for sequence, trade in enumerate(trades, start=1):
            trade_records.append(
                (
                    run_id,
                    sequence,
                    str(trade.get("symbol", "")),
                    str(trade.get("direction", "")),
                    str(trade.get("entry_timestamp", "")),
                    str(trade.get("exit_timestamp", "")),
                    float(trade.get("entry_price", 0.0)),
                    float(trade.get("exit_price", 0.0)),
                    float(trade.get("initial_stop", 0.0)),
                    float(trade.get("final_stop", 0.0)),
                    float(trade.get("shares", 0.0)),
                    float(trade.get("pnl", 0.0)),
                    float(trade.get("pnl_pct", 0.0)),
                    float(trade.get("r_multiple", 0.0)),
                    str(trade.get("exit_reason", "")),
                    trade.get("position_cost"),
                    trade.get("entry_cash_before"),
                    trade.get("entry_equity_before"),
                    trade.get("entry_open_risk_before"),
                    trade.get("entry_remaining_stop_budget"),
                    trade.get("entry_allowed_risk"),
                    json.dumps(self._json_safe(trade), ensure_ascii=True),
                    created_at,
                )
            )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO backtest_runs (
                    id, start_date, end_date, initial_capital, risk_pct,
                    max_total_open_risk_pct, max_open_positions,
                    assumptions_json, summary_json, created_at
                )
                VALUES (
                    :id, :start_date, :end_date, :initial_capital, :risk_pct,
                    :max_total_open_risk_pct, :max_open_positions,
                    :assumptions_json, :summary_json, :created_at
                )
                """,
                row,
            )
            connection.execute("DELETE FROM backtest_trades WHERE run_id = ?", (run_id,))
            if trade_records:
                connection.executemany(
                    """
                    INSERT INTO backtest_trades (
                        run_id, sequence, symbol, direction, entry_timestamp, exit_timestamp,
                        entry_price, exit_price, initial_stop, final_stop, shares, pnl, pnl_pct,
                        r_multiple, exit_reason, position_cost, entry_cash_before,
                        entry_equity_before, entry_open_risk_before, entry_remaining_stop_budget,
                        entry_allowed_risk, trade_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    trade_records,
                )
        return run_id
