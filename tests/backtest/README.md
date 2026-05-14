# Elder Triple Screen Model Backtest

Complete backtest framework for running multi-phase backtests on us_top_300 universe with full risk management (2% per trade, 6% open cap, 1.5x leverage).

## Quick Start

### Run Full Backtest (All Phases)
```bash
cd /Users/jay/workspace/my_github/triple_screen
python -m tests.backtest.backtest_runner --all
```

### Run Specific Phases
```bash
# Phase 1: Data preparation & validation
python -m tests.backtest.backtest_runner --phase 1

# Phase 2: Engine setup & validation
python -m tests.backtest.backtest_runner --phase 2

# Phase 3: Execute backtest & validate
python -m tests.backtest.backtest_runner --phase 3

# Phase 4: Generate analysis reports
python -m tests.backtest.backtest_runner --phase 4
```

### Run with Custom Configuration
```bash
python -m tests.backtest.backtest_runner --all --config tests/backtest/backtest_config.yaml
```

### Run on Subset (Quick Validation)
```bash
# Test on 5 specific symbols, shorter date range
python -m tests.backtest.backtest_runner --all \
  --symbols AAPL MSFT GOOGL TSLA AMZN \
  --start-date 2025-01-01 --end-date 2025-12-31
```

### Run with Verbose Output
```bash
python -m tests.backtest.backtest_runner --all --verbose
```

---

## Phases Explained

### **Phase 1: Data Acquisition & Preparation**

**What it does:**
- Loads universe symbols from `config/universe_us_top300.yaml`
- Prepares for downloading daily + weekly OHLCV data
- Validates date range and symbol list
- Does NOT download data (too large for test; requires Alpaca API)

**When to run:** 
- First time setup
- When updating symbol universe
- Validates data acquisition is ready

**Output:** Symbol count, date range verification

**Next step:** Use `src/clients/alpaca.py` to bulk download data to SQLite

---

### **Phase 2: Engine Setup**

**What it does:**
- Validates backtest configuration
- Checks that all required indicators are available
- Verifies database connectivity
- Validates risk parameters (2%, 6%, 1.5x)
- Does NOT modify any data

**When to run:** 
- Before Phase 3
- After changing config parameters
- Verification that system is ready to backtest

**Output:** Configuration validation report, status = READY

---

### **Phase 3: Execution & Validation**

**What it does:**
- Runs daily simulation from start_date to end_date
- For each trading day:
  1. EOD analysis: Apply weekly + daily screens
  2. Watchlist filtering: Candidates that passed screens
  3. Entry triggers: Check hourly bars for EMA/breakout confirmation
  4. Position sizing: Enforce 2% per trade, 6% cap, 1.5x margin
  5. Stop updates: ATR 1x with monotonic ratchet
  6. Exit detection: Stop hits, take profits
  7. P&L calculation
- Validates all risk constraints
- Spot-checks 20 random trades

**When to run:** 
- After Phase 2 is ready
- When you want full backtest simulation

**Output:** 
- Summary metrics (trades, win rate, P&L, drawdown, Sharpe)
- Validation report
- Trade-by-trade details (if enabled)

---

### **Phase 4: Analysis & Reporting**

**What it does:**
- Generates equity curve (daily equity progression)
- Monthly/yearly P&L breakdown
- Symbol-level performance analysis
- Risk timeline (leverage, open risk vs 6% cap)
- Creates JSON output for visualization

**When to run:** 
- After Phase 3 completes
- When you want detailed analysis

**Output:**
- `data/backtest_<timestamp>_analysis.json`
- Charts ready for visualization
- Risk timeline data

---

## Configuration

Default configuration: `tests/backtest/backtest_config.yaml`

### Key Parameters

```yaml
account:
  starting_equity: 100000.0          # Starting capital

risk:
  per_trade_pct: 0.02                # 2% max loss per trade
  max_open_pct: 0.06                 # 6% total open risk cap

leverage:
  ratio: 1.5                          # Allow 1.5x buying power

data:
  start_date: "2023-01-01"           # Backtest period
  end_date: "2026-05-01"

entry:
  methods: [ema_penetration, previous_day_breakout]
  confirmation: hourly

stops:
  method: atr_1x                     # ATR stop (1x only)
  trailing_rule: monotonic_ratchet   # Never move backward
```

### Override via CLI
```bash
python -m tests.backtest.backtest_runner --all \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --symbols AAPL MSFT TSLA
```

---

## Output Files

After running backtest:

```
data/
├── backtest_20260514_120000_results.json    # Full results
└── backtest_20260514_120000_analysis.json   # Analysis data
```

### Results JSON Structure
```json
{
  "phase_1": {...},
  "phase_2": {...},
  "phase_3": {
    "summary_metrics": {
      "total_trades": 42,
      "win_rate": 0.62,
      "final_equity": 125000,
      "max_drawdown": -8.5,
      "sharpe_ratio": 1.24
    }
  },
  "phase_4": {...}
}
```

---

## Examples

### Quick Test Run (5 symbols, 3 months)
```bash
python -m tests.backtest.backtest_runner --all \
  --symbols AAPL MSFT TSLA AMZN NVDA \
  --start-date 2025-10-01 \
  --end-date 2025-12-31 \
  --verbose
```

### Full Production Backtest
```bash
python -m tests.backtest.backtest_runner --all \
  --config tests/backtest/backtest_config.yaml \
  --verbose
```

### Run Only Phase 3 (after data is ready)
```bash
python -m tests.backtest.backtest_runner --phase 3 --verbose
```

### Re-run Analysis on Existing Results
```bash
python -m tests.backtest.backtest_runner --phase 4 \
  --verbose
```

---

## Troubleshooting

### Phase 1 Fails
- **Issue**: Symbol loading fails
- **Solution**: Verify `config/universe_us_top300.yaml` exists and is valid
- **Check**: `python -c "import yaml; yaml.safe_load(open('config/universe_us_top300.yaml'))"`

### Phase 2 Fails
- **Issue**: Indicators not found
- **Solution**: Verify `src/indicators.py` has all required functions
- **Check**: `python -c "import sys; sys.path.insert(0, 'src'); import indicators; print(dir(indicators))"`

### Phase 3 Fails
- **Issue**: Database connection fails
- **Solution**: Check SQLite file location and permissions
- **Check**: `ls -la data/` and verify database exists

### No Results
- **Issue**: Backtest runs but produces no trades
- **Solution**: 
  - Check data exists for symbols
  - Verify date range has market data
  - Try Phase 3 on shorter date range first

---

## Architecture

### Folder Structure
```
tests/backtest/
├── __init__.py
├── backtest_runner.py          # Main CLI orchestrator
├── backtest_config.yaml        # Default configuration
├── README.md                   # This file
├── phases/
│   ├── __init__.py
│   ├── phase_1_data_acquisition.py
│   ├── phase_2_engine_setup.py
│   ├── phase_3_execution.py
│   └── phase_4_analysis.py
└── utils/
    ├── __init__.py
    ├── backtest_fixtures.py    # Config loading
    ├── data_validator.py       # Data validation
    └── result_analyzer.py      # Results analysis
```

### Design Principles

1. **Modular**: Each phase is independent
2. **Testable**: Each phase can run separately
3. **Configurable**: YAML config + CLI overrides
4. **Isolated**: Backtest results separate from live trades
5. **Reusable**: Uses existing indicators, models, storage

---

## Key Metrics Tracked

### Account-Level
- Starting equity: $100,000
- Final equity
- Total P&L
- Max drawdown
- Sharpe ratio
- Calmar ratio

### Position-Level
- Entry date, price, stop
- Exit date, price, reason
- Shares, direction
- Gross/net P&L (with $1 commission)
- R-multiple (reward / risk)

### Risk Metrics
- Per-trade risk: Never > 2% equity
- Open risk: Never > 6% equity
- Leverage used: Never > 1.5x
- Consecutive wins/losses

### Trade-Level Details (if enabled)
- Entry trigger (EMA vs breakout)
- Stop update history
- Days held
- Intraday high/low
- Win/loss streak

---

## Advanced Usage

### Compare Two Backtest Runs
```python
import json

results1 = json.load(open("data/backtest_run1_results.json"))
results2 = json.load(open("data/backtest_run2_results.json"))

m1 = results1["phase_3"]["summary_metrics"]
m2 = results2["phase_3"]["summary_metrics"]

print(f"Run 1 Win Rate: {m1['win_rate']*100:.1f}%")
print(f"Run 2 Win Rate: {m2['win_rate']*100:.1f}%")
```

### Extract Trade History
```python
import json
import pandas as pd

results = json.load(open("data/backtest_<timestamp>_results.json"))
trades = results["phase_3"]["trades"]  # When included
df = pd.DataFrame(trades)

# Analysis
print(df.groupby("symbol")["pnl"].sum().sort_values(ascending=False))
```

---

## Performance Expectations

- **Phase 1**: < 1 second (loading only, no download)
- **Phase 2**: < 2 seconds (validation)
- **Phase 3**: 10-30 seconds (depends on universe size, date range)
- **Phase 4**: < 5 seconds (analysis)

Total for quick test: < 1 minute  
Total for full backtest: 5-10 minutes (after Phase 1 data is ready)

---

## Next Steps

1. **Complete Phase 1**: Download data via Alpaca API to SQLite
2. **Implement Phase 3**: Full simulation loop with screens, triggers, stops
3. **Validate Results**: Compare to legacy backtest if available
4. **Deploy Phase 4**: Charts and visualizations in web UI

See [plan_backtest.md](../../plan_backtest.md) for full implementation roadmap.
