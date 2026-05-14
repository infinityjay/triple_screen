# Backtest Plan: Elder Triple Screen Model (us_top_300, 2023-2026)

## TL;DR
Run a complete backtest of the elder_force model on us_top_300 stocks from 2023-01-01 to 2026-05-01, simulating daily EOD analysis, order placement based on hourly triggers (EMA + breakout), daily stop updates, and full risk management (2% per trade, 6% account cap, 1.5x position sizing). Store summary results in database, not intraday details.

---

## Steps

### Phase 1: Data Acquisition & Preparation
**Depends on:** None | **Parallel:** Can run in parallel with phase 2 planning

1. **Download historical OHLCV data**
   - Source: Alpaca API
   - Symbols: All from us_top_300 config (~/config/universe_us_top300.yaml)
   - Timeframes: Daily + Weekly
   - Date range: 2023-01-01 to 2026-05-01 (inclusive)
   - Action: Modify or create new script based on existing alpaca.py client
   - Storage: Batch write to SQLite price_bars table (symbol, timeframe, timestamp composite key)
   - Note: Existing codebase has incremental update logic; may reuse or adapt

2. **Verify data completeness**
   - Check for gaps (missing bars, adjusted splits/dividends)
   - Compare bar count per symbol (should be ~756 daily bars, ~208 weekly bars)
   - Log any symbols with insufficient data

---

### Phase 2: Backtest Engine Enhancement
**Depends on:** None | **Parallel:** With Phase 1

3. **Configure backtest parameters**
   - File: Extend or create config override for backtest run
   - Parameters:
     - **Account**: Starting equity $100,000
     - **Risk per trade**: 2% ($2,000 max loss per position)
     - **Max open risk cap**: 6% ($6,000 total across all open positions)
     - **Position sizing mode**: 1.5x margin (deploy up to 1.5x equity as buying power, honor open risk cap)
     - **Leverage**: Allow borrowing, enforce margin requirement
     - **Date range**: 2023-01-01 to 2026-05-01
     - **Model**: elder_force only
     - **Entry triggers**: Both EMA penetration AND previous-day breakout (any match triggers)
     - **Universe**: us_top_300
   
4. **Adapt backtest engine** (src/backtest_triple_screen.py modifications)
   - **Data loading**: Ensure daily + weekly bars load correctly for entire date range
   - **Session loop**: For each trading day from 2023-01-01 to 2026-05-01:
     - Daily workflow:
       1. Run EOD analysis: Apply weekly + daily screens → generate qualified_candidates
       2. Build watchlist: Filter candidates that passed screens (not same as signal detection)
       3. Check hourly bars: For each candidate on watchlist, check if hourly price touched/closed through entry points (EMA penetration OR breakout)
       4. Entry decision: If hourly bars confirm entry, place order (simulated execution at next day open or based on trigger time)
       5. Stop management: Update all open position stops using ATR stop with monotonic ratchet logic
       6. Exit detection: Check if any position hit stop loss or take profit target
       7. Record state: Daily P&L, equity, open positions (in memory, not all to DB)
   
   - **Position management**:
     - Initial stop: Closest price to entry (ATR-based for consistency)
     - Daily stop update: Use ATR stop only with monotonic ratchet (move only in favorable direction: higher for LONG, lower for SHORT)
     - Risk budget tracking: 
       - Per position: risk = (entry - stop) × shares
       - Account total: Sum of all open position risks, cap at 6% equity
       - Remaining budget = 6% equity - current open risk
       - Reject new entries if would exceed 6% cap
   
   - **Position sizing** (1.5x margin mode):
     - For new entry with 2% risk ($2,000):
       - risk_per_share = |entry_price - stop_price|
       - base_shares = risk_per_share > 0 ? 2000 / risk_per_share : 0
       - max_shares_by_cash = floor(1.5 × equity / entry_price)  ← allows borrowing up to 1.5x
       - max_shares_by_risk_budget = remaining_stop_budget / risk_per_share
       - final_shares = min(base_shares, max_shares_by_cash, max_shares_by_risk_budget)
   
   - **Entry execution logic**:
     - Candidate enters watchlist when: weekly + daily screens both pass (QUALIFIED state)
     - Signal trigger when: hourly bars confirm entry point AND remaining open risk budget allows trade
     - Execution price: 
       - Ideally next-day open or trigger price
       - If backtest uses daily bars only, assume next-day open at yesterday's close + typical gap
       - Or conservative: use next day's actual open from data

5. **Extend backtest output metrics**
   - Daily metrics: Date, equity, cash, open positions count, total open risk, remaining stop budget, daily P&L
   - Trade metrics: Entry date, symbol, direction, entry price, initial stop, exit date, exit price, exit reason (stop hit / take profit), shares, gross P&L, net P&L (minus commission), R-multiple
   - Summary metrics:
     - Total trades, win rate, max drawdown, Sharpe ratio
     - Avg trade duration, avg risk per trade, avg R-multiple
     - Best/worst trade, consecutive wins/losses
     - Risk-adjusted metrics (Calmar ratio, sortino)

---

### Phase 3: Backtest Execution & Validation
**Depends on:** Phase 1 (data ready) + Phase 2 (engine configured)

6. **Run backtest**
   - Execute backtest_triple_screen.py with us_top_300 config and date range
   - Monitor for errors (data gaps, calculation errors)
   - Generate run ID for tracking

7. **Store results to database**
   - Create new backtest_runs entry with:
     - Run date, model (elder_force), universe (us_top_300), date range, account assumptions
     - Summary metrics (total trades, win rate, max DD, Sharpe, final equity)
   - Insert trade records into trades table or new backtest_trades table (to avoid mixing live trades)
   - Optional: Append daily P&L to backtest_daily_equity table for chart generation

8. **Validation checks**
   - Verify no trade violates 2% per-trade risk
   - Verify cumulative open risk never exceeded 6%
   - Spot-check 10-20 random trades: entry price, stop, shares, P&L calculations
   - Compare results to legacy_maxpos2 run if available (sanity check)
   - Review largest trades: verify entry was truly qualified + triggered
   - Check for any edge cases (gaps, overnight holding, market holidays)

---

### Phase 4: Analysis & Reporting
**Depends on:** Phase 3 (backtest complete)

9. **Generate analysis reports**
   - Equity curve: Starting $100k → final equity, drawdown visualization
   - Win/loss distribution: Histogram of trade sizes, durations, R-multiples
   - Monthly/yearly P&L breakdown: Identify strong/weak periods
   - Symbol-level performance: Which symbols generated most profit, which had losses
   - Risk analysis:
     - Did 2% per-trade cap ever bind (reject trades due to budget)?
     - How many positions ever simultaneously open?
     - Max leverage reached (peak open risk / equity)?

10. **Web UI visualization** (optional)
    - Extend frontend (journal.html, analysis.html) to display backtest results
    - Charts: Equity curve, drawdown, monthly returns, trade scatter plot
    - Table: All trades with entry/exit reasons, P&L per symbol
    - Risk dashboard: Daily open risk vs. 6% cap, position count timeline

---

## Relevant Files

- `src/backtest_triple_screen.py` — Backtesting engine (modify: data loading, daily session loop, position sizing for 1.5x margin, stop management workflow)
- `src/trading_models.py` — Weekly + daily screen logic (elder_force should already be implemented, verify passes/rejects correctly)
- `src/indicators.py` — All stop calculation methods (SafeZone, Nick, Chandelier, ATR, etc.) - should be reusable
- `config/universe_us_top300.yaml` — List of symbols for backtest universe
- `src/storage/sqlite.py` — Database schema (adapt trades table or create backtest_trades for isolation)
- `src/clients/alpaca.py` — Data download client (may need modification for bulk historical fetch)
- `config/settings.yaml` — Parameters (MACD periods, EMA periods, Force Index EMA, stop methods defaults, risk_per_trade_pct, open_risk_cap_pct)

---

## Verification

1. **Data validation**
   - All us_top_300 symbols have daily bars from 2023-01-01 to 2026-05-01
   - No missing OHLCV data (check for NaNs)
   - Weekly bars are properly aggregated/aligned

2. **Logic spot-checks**
   - Manually trace 3-5 example trades:
     - Verify entry candidate passed both weekly + daily screens
     - Verify hourly trigger matched EMA or breakout method
     - Verify position sizing respected 2% rule and 6% cap
     - Verify stop was updated daily and never went backwards
     - Verify exit P&L was calculated correctly
   - Run backtest on single symbol or small universe first (5-10 symbols) to validate

3. **Risk management validation**
   - Assert no trade risk > 2% of entry equity
   - Assert sum of all open trades' risk never exceeded 6% of equity at any time
   - Assert position sizing never exceeded 1.5x leverage (buying power / cash never > 1.5)
   - Verify cash never went negative (if margin calculation incorrect, this would catch it)

4. **Comparison**
   - If legacy backtest exists (backtest_legacy_maxpos2_*.json), compare:
     - If same universe + similar dates → should see similar equity curve shape
     - If different → document the differences (model change, risk params change, etc.)

5. **Output validation**
   - Final equity > $100k OR < $100k (check direction makes sense for strategy)
   - Win rate is reasonable (not 100% or 0%)
   - Sharpe ratio > 0 (positive risk-adjusted returns)
   - Max drawdown < 20% (doesn't wipe out account)

---

## Critical Architecture Decisions

1. **Entry execution timing**: Backtest will use next-day open or trigger time (if hourly trigger during day, use that price; if EOD signal, use next open).

2. **1.5x margin logic**: Position sizing will allow deploying up to 1.5x of current equity as buying power, subject to:
   - Remaining cash ≥ 0 (can't go negative)
   - Stop loss budget cap at 6% still enforced
   - This may result in fewer positions if both caps bind simultaneously

3. **Database isolation**: Backtest trades stored in separate table or marked with backtest_run_id to avoid mixing with live/paper trading records.

4. **Stop update frequency**: Updates happen once per day (at EOD), not intraday. Existing JournalManager can be reused for this.

5. **Entry methods combined**: Both EMA penetration AND previous-day breakout calculated daily; if either triggers on hourly bars, entry placed (first trigger wins).

6. **ATR stop only**: Daily stop updates use ATR stop (1x) with monotonic ratchet - never moving backwards. For LONG positions stops can only move higher; for SHORT positions stops can only move lower.

---

## Further Considerations

1. **Intraday data handling**: Current backtest uses daily bars. To properly model "hourly confirmation", we need:
   - Option A: Load hourly bars and check each hour if price touched/closed through entry levels
   - Option B: Use daily high/low as proxy for intraday range (faster, less data)
   - **Recommendation**: Start with B (daily H/L) for speed, validate

2. **Commission & slippage**: Are these modeled in backtest, or assumed frictionless?
   - Existing code has commission fields; confirm if should be applied (e.g., $0-5 per trade)
   - Slippage: Fixed spread (e.g., -$0.01 entry, +$0.01 exit) or % of entry?
   - **Recommendation**: $1 per trade for both buy and sell.

3. **Earnings blackout**: Existing system has earnings window (e.g., -3 to +3 days of earnings event). Should backtest respect this?
   - If yes, need earnings_events table populated with historical dates (may not be complete)
   - **Recommendation**: Don't consider earnings just follow the stop.