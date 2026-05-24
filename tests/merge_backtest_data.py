#!/usr/bin/env python3
"""
Merge backtest data from ~/Desktop/triple_screen.db to data/triple_screen.db
Deduplicates based on primary keys and avoids conflicts
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime

# Database paths
CURRENT_DB = Path("/Users/jay/workspace/my_github/triple_screen/data/triple_screen.db")
DESKTOP_DB = Path.home() / "Desktop" / "triple_screen.db"

# Tables to merge (in order of dependency)
BACKTEST_TABLES = [
    "backtest_runs",
    "backtest_trades",
]

SUPPORTING_TABLES = [
    "price_bars",
    "qualified_candidates",
    "weekly_indicators",
    "daily_indicators",
    "hourly_indicators",
]

def connect_db(db_path):
    """Connect to database and enable foreign keys."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def get_table_info(cursor, table_name):
    """Get column names and primary key for a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    
    cursor.execute(f"PRAGMA table_info({table_name})")
    pk_cols = [col[1] for col in columns if col[5] > 0]  # col[5] is pk position
    all_cols = [col[1] for col in columns]
    
    return all_cols, pk_cols

def get_row_count(cursor, table_name):
    """Get row count for a table."""
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    return cursor.fetchone()[0]

def analyze_databases():
    """Analyze both databases before merge."""
    print("=" * 70)
    print("DATABASE ANALYSIS")
    print("=" * 70)
    
    current_conn = connect_db(CURRENT_DB)
    desktop_conn = connect_db(DESKTOP_DB)
    
    current_cursor = current_conn.cursor()
    desktop_cursor = desktop_conn.cursor()
    
    print(f"\nCurrent DB: {CURRENT_DB}")
    print(f"Desktop DB: {DESKTOP_DB}\n")
    
    # Analyze backtest tables
    print("BACKTEST TABLES:")
    print("-" * 70)
    for table in BACKTEST_TABLES:
        current_count = get_row_count(current_cursor, table)
        desktop_count = get_row_count(desktop_cursor, table)
        
        all_cols, pk_cols = get_table_info(current_cursor, table)
        
        print(f"\n{table}:")
        print(f"  Current DB: {current_count:6d} rows")
        print(f"  Desktop DB: {desktop_count:6d} rows")
        print(f"  Primary Key: {', '.join(pk_cols)}")
        print(f"  Columns: {', '.join(all_cols[:5])}{'...' if len(all_cols) > 5 else ''}")
    
    # Analyze supporting tables
    print("\n\nSUPPORTING DATA TABLES:")
    print("-" * 70)
    for table in SUPPORTING_TABLES:
        try:
            current_count = get_row_count(current_cursor, table)
            desktop_count = get_row_count(desktop_cursor, table)
            
            all_cols, pk_cols = get_table_info(current_cursor, table)
            
            print(f"\n{table}:")
            print(f"  Current DB: {current_count:6d} rows")
            print(f"  Desktop DB: {desktop_count:6d} rows")
            print(f"  Primary Key: {', '.join(pk_cols)}")
        except Exception as e:
            print(f"\n{table}: ERROR - {e}")
    
    current_conn.close()
    desktop_conn.close()

def merge_backtest_runs():
    """Merge backtest_runs table with deduplication."""
    print("\n" + "=" * 70)
    print("MERGING: backtest_runs")
    print("=" * 70)
    
    current_conn = connect_db(CURRENT_DB)
    desktop_conn = connect_db(DESKTOP_DB)
    
    current_cursor = current_conn.cursor()
    desktop_cursor = desktop_conn.cursor()
    
    # Get all runs from desktop
    desktop_cursor.execute("SELECT id FROM backtest_runs ORDER BY created_at")
    desktop_runs = [row[0] for row in desktop_cursor.fetchall()]
    
    # Check which ones already exist in current
    skipped = 0
    inserted = 0
    
    for run_id in desktop_runs:
        current_cursor.execute("SELECT 1 FROM backtest_runs WHERE id = ?", (run_id,))
        if current_cursor.fetchone():
            skipped += 1
            continue
        
        # Copy the run
        desktop_cursor.execute("SELECT * FROM backtest_runs WHERE id = ?", (run_id,))
        row = desktop_cursor.fetchone()
        
        current_cursor.execute("""
            INSERT INTO backtest_runs 
            (id, start_date, end_date, initial_capital, risk_pct, 
             max_total_open_risk_pct, max_open_positions, assumptions_json, 
             summary_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, row)
        inserted += 1
    
    current_conn.commit()
    print(f"✓ Inserted: {inserted} new backtest runs")
    print(f"✓ Skipped:  {skipped} existing runs (duplicates)")
    
    current_conn.close()
    desktop_conn.close()

def merge_backtest_trades():
    """Merge backtest_trades table with deduplication."""
    print("\n" + "=" * 70)
    print("MERGING: backtest_trades")
    print("=" * 70)
    
    current_conn = connect_db(CURRENT_DB)
    desktop_conn = connect_db(DESKTOP_DB)
    
    current_cursor = current_conn.cursor()
    desktop_cursor = desktop_conn.cursor()
    
    # Get all trades from desktop
    desktop_cursor.execute("""
        SELECT run_id, sequence FROM backtest_trades 
        ORDER BY run_id, sequence
    """)
    desktop_trades = [(row[0], row[1]) for row in desktop_cursor.fetchall()]
    
    skipped = 0
    inserted = 0
    
    for run_id, sequence in desktop_trades:
        # Check if trade already exists
        current_cursor.execute(
            "SELECT 1 FROM backtest_trades WHERE run_id = ? AND sequence = ?",
            (run_id, sequence)
        )
        if current_cursor.fetchone():
            skipped += 1
            continue
        
        # Copy the trade
        desktop_cursor.execute(
            """SELECT * FROM backtest_trades WHERE run_id = ? AND sequence = ?""",
            (run_id, sequence)
        )
        row = desktop_cursor.fetchone()
        
        current_cursor.execute("""
            INSERT INTO backtest_trades
            (run_id, sequence, symbol, direction, entry_timestamp, exit_timestamp,
             entry_price, exit_price, initial_stop, final_stop, shares, pnl, pnl_pct,
             r_multiple, exit_reason, position_cost, entry_cash_before, 
             entry_equity_before, entry_open_risk_before, entry_remaining_stop_budget,
             entry_allowed_risk, trade_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, row)
        inserted += 1
    
    current_conn.commit()
    print(f"✓ Inserted: {inserted} new trades")
    print(f"✓ Skipped:  {skipped} existing trades (duplicates)")
    
    current_conn.close()
    desktop_conn.close()

def merge_price_bars():
    """Merge price_bars table with deduplication."""
    print("\n" + "=" * 70)
    print("MERGING: price_bars (supporting data)")
    print("=" * 70)
    
    current_conn = connect_db(CURRENT_DB)
    desktop_conn = connect_db(DESKTOP_DB)
    
    current_cursor = current_conn.cursor()
    desktop_cursor = desktop_conn.cursor()
    
    # Get unique symbol+timeframe+timestamp combinations from desktop
    desktop_cursor.execute("""
        SELECT symbol, timeframe, timestamp FROM price_bars
        ORDER BY symbol, timeframe, timestamp
    """)
    desktop_bars = [(row[0], row[1], row[2]) for row in desktop_cursor.fetchall()]
    
    skipped = 0
    inserted = 0
    
    for symbol, timeframe, timestamp in desktop_bars:
        # Check if bar already exists
        current_cursor.execute(
            "SELECT 1 FROM price_bars WHERE symbol = ? AND timeframe = ? AND timestamp = ?",
            (symbol, timeframe, timestamp)
        )
        if current_cursor.fetchone():
            skipped += 1
            continue
        
        # Copy the bar
        desktop_cursor.execute(
            """SELECT * FROM price_bars WHERE symbol = ? AND timeframe = ? AND timestamp = ?""",
            (symbol, timeframe, timestamp)
        )
        row = desktop_cursor.fetchone()
        
        current_cursor.execute("""
            INSERT INTO price_bars
            (symbol, timeframe, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, row)
        inserted += 1
    
    current_conn.commit()
    print(f"✓ Inserted: {inserted} new price bars")
    print(f"✓ Skipped:  {skipped} existing bars (duplicates)")
    
    current_conn.close()
    desktop_conn.close()

def merge_qualified_candidates():
    """Merge qualified_candidates table with deduplication."""
    print("\n" + "=" * 70)
    print("MERGING: qualified_candidates (supporting data)")
    print("=" * 70)
    
    current_conn = connect_db(CURRENT_DB)
    desktop_conn = connect_db(DESKTOP_DB)
    
    current_cursor = current_conn.cursor()
    desktop_cursor = desktop_conn.cursor()
    
    # Get unique symbol+session_date combinations
    desktop_cursor.execute("""
        SELECT symbol, session_date FROM qualified_candidates
        ORDER BY symbol, session_date
    """)
    desktop_candidates = [(row[0], row[1]) for row in desktop_cursor.fetchall()]
    
    skipped = 0
    inserted = 0
    
    for symbol, session_date in desktop_candidates:
        current_cursor.execute(
            "SELECT 1 FROM qualified_candidates WHERE symbol = ? AND session_date = ?",
            (symbol, session_date)
        )
        if current_cursor.fetchone():
            skipped += 1
            continue
        
        # Copy the candidate
        desktop_cursor.execute(
            """SELECT * FROM qualified_candidates WHERE symbol = ? AND session_date = ?""",
            (symbol, session_date)
        )
        row = desktop_cursor.fetchone()
        
        current_cursor.execute("""
            INSERT INTO qualified_candidates
            (symbol, session_date, direction, signal_score, reason, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, row)
        inserted += 1
    
    current_conn.commit()
    print(f"✓ Inserted: {inserted} new candidates")
    print(f"✓ Skipped:  {skipped} existing candidates (duplicates)")
    
    current_conn.close()
    desktop_conn.close()

def merge_indicators():
    """Merge indicator tables with deduplication."""
    indicator_tables = [
        "weekly_indicators",
        "daily_indicators",
        "hourly_indicators",
    ]
    
    for table_name in indicator_tables:
        print("\n" + "=" * 70)
        print(f"MERGING: {table_name} (supporting data)")
        print("=" * 70)
        
        current_conn = connect_db(CURRENT_DB)
        desktop_conn = connect_db(DESKTOP_DB)
        
        current_cursor = current_conn.cursor()
        desktop_cursor = desktop_conn.cursor()
        
        # Get all symbols from desktop (symbol is PRIMARY KEY)
        desktop_cursor.execute(f"SELECT symbol FROM {table_name} ORDER BY symbol")
        desktop_symbols = [row[0] for row in desktop_cursor.fetchall()]
        
        skipped = 0
        inserted = 0
        
        for symbol in desktop_symbols:
            current_cursor.execute(
                f"SELECT 1 FROM {table_name} WHERE symbol = ?",
                (symbol,)
            )
            if current_cursor.fetchone():
                skipped += 1
                continue
            
            # Copy the indicator row
            desktop_cursor.execute(
                f"SELECT * FROM {table_name} WHERE symbol = ?",
                (symbol,)
            )
            row = desktop_cursor.fetchone()
            
            # Get column names dynamically
            desktop_cursor.execute(f"PRAGMA table_info({table_name})")
            columns = [col[1] for col in desktop_cursor.fetchall()]
            col_placeholders = ", ".join(columns)
            value_placeholders = ", ".join(["?"] * len(columns))
            
            current_cursor.execute(f"""
                INSERT INTO {table_name} ({col_placeholders})
                VALUES ({value_placeholders})
            """, row)
            inserted += 1
        
        current_conn.commit()
        print(f"✓ Inserted: {inserted} new indicators")
        print(f"✓ Skipped:  {skipped} existing indicators (duplicates)")
        
        current_conn.close()
        desktop_conn.close()

def main():
    """Execute the full merge process."""
    print("\n" + "=" * 70)
    print("BACKTEST DATA MERGE: ~/Desktop/triple_screen.db → data/triple_screen.db")
    print("=" * 70)
    
    try:
        # Step 1: Analyze
        analyze_databases()
        
        # Step 2: Merge backtest tables (priority)
        merge_backtest_runs()
        merge_backtest_trades()
        
        # Step 3: Merge supporting data
        merge_price_bars()
        merge_qualified_candidates()
        merge_indicators()
        
        # Final summary
        print("\n" + "=" * 70)
        print("MERGE COMPLETE!")
        print("=" * 70)
        
        # Final verification
        current_conn = connect_db(CURRENT_DB)
        current_cursor = current_conn.cursor()
        
        print("\nFinal row counts in current database:")
        for table in BACKTEST_TABLES + SUPPORTING_TABLES:
            try:
                current_cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = current_cursor.fetchone()[0]
                print(f"  {table:30s}: {count:8d} rows")
            except Exception as e:
                print(f"  {table:30s}: ERROR - {e}")
        
        current_conn.close()
        
        print("\n✓ Merge completed successfully!")
        
    except Exception as e:
        print(f"\n✗ Error during merge: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()