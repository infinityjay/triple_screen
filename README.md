# Triple Screen Scanner

A multi-timeframe signal scanner based on Alexander Elder's Triple Screen trading system. Scans a universe of up to 300 US equities, runs hourly intraday trigger detection, and delivers real-time Telegram alerts.

Design priorities: clear separation of concerns, all business parameters in one config file, secrets only in `.env`.

- All strategy parameters live in `config/settings.yaml`
- Secrets are kept in `.env` only — never committed
- Run with `python src/scanner.py`
- Compress a large universe down to a focused watchlist with `python src/universe_optimizer.py`

## Directory Structure

```text
triple_screen/
├── config/
│   ├── settings.yaml                      # Main config file (non-sensitive)
│   ├── universe_us_top300.yaml            # Default universe (300 symbols)
│   └── universe_us_top100_optimized.yaml  # Optimized universe (optional)
├── data/                                  # Backtest data and SQLite storage (created at runtime)
├── deploy/
│   └── aws/                               # EC2 + systemd deployment templates
├── frontend/
│   └── trade_journal/                     # Journal Web UI static files
├── logs/                                  # Log directory (created at runtime)
├── requirements.txt
├── src/
│   ├── scanner.py                         # CLI entry point
│   ├── runner.py                          # Scheduler and scan loop
│   ├── scan_engine.py                     # Scan orchestration
│   ├── indicators.py                      # Triple-screen indicators and scoring
│   ├── trading_models.py                  # Trading model definitions
│   ├── universe_optimizer.py              # Universe optimizer
│   ├── backtest_triple_screen.py          # Historical backtester
│   ├── clients/
│   │   ├── alpaca.py                      # Alpaca market data client
│   │   ├── earnings.py                    # Earnings calendar client
│   │   └── telegram.py                    # Telegram alert builder
│   ├── config/
│   │   ├── loader.py                      # YAML + .env config loader
│   │   └── schema.py                      # Config dataclass definitions
│   ├── journal/
│   │   ├── server.py                      # FastAPI Journal Server
│   │   ├── service.py                     # Open-position stop management
│   │   └── technical_analysis.py          # Single-symbol technical analysis engine
│   └── storage/
│       └── sqlite.py                      # SQLite storage layer
├── tests/                                 # Unit tests
└── .env.example                           # Secrets template
```

## Configuration

All business configuration goes in `config/settings.yaml`. The symbol universe is in `config/universe_us_top300.yaml`.

Secrets go in `.env`:

```env
ALPACA_API_KEY_ID=your_alpaca_api_key_id
ALPACA_API_SECRET_KEY=your_alpaca_api_secret_key
ALPACA_MARKET_DATA_BASE_URL=https://data.alpaca.markets/v2
ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets/v2
ALPHAVANTAGE_API_KEY=your_alpha_vantage_api_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

`.env` is listed in `.gitignore` and will never be committed.

## Quick Start

1. Install dependencies

```bash
pip install -r requirements.txt
```

2. Set up secrets

```bash
cp .env.example .env
```

3. Edit `config/settings.yaml`

Key settings you can tune:

- Universe size
- MACD / RSI / breakout parameters
- Candidate pool reward-risk threshold, earnings blackout window, strong-divergence threshold
- Number of candidates to display and triggered alerts to push
- Worker concurrency, scan frequency, log path
- SQLite database path
- Telegram enabled / disabled

To add or remove symbols manually, edit the `symbols` list in `config/universe_us_top300.yaml`.

4. Run a single scan

Update the candidate pool after market close:

```bash
python src/scanner.py --once --mode eod
```

Scan for intraday triggers against the previous session's candidates:

```bash
python src/scanner.py --once --mode intraday
```

Auto-select mode based on current market session:

```bash
python src/scanner.py --once
```

Validate the full pipeline without sending Telegram alerts:

```bash
python src/scanner.py --once --dry-run
```

5. Run continuously

```bash
python src/scanner.py --loop
```

## Runtime Behaviour

- Weekly, daily, and hourly bars are cached in local SQLite and updated incrementally — subsequent scans do not re-fetch the full history.
- Alpaca market data uses `feed: iex` by default, which works with a free Alpaca account.
- The base URL accepts both `https://.../v2` and the root domain without `/v2` — the client normalises it automatically.
- Hourly bar incremental refresh uses a precise time window rather than a full date range.
- At scan startup, bars for the entire universe are batch-fetched to warm the cache before per-symbol indicator calculations begin, avoiding hundreds of individual API requests.

## Universe Optimizer

`src/universe_optimizer.py` compresses a large universe into a focused candidate pool. It scores each symbol against the following dimensions using your existing Alpaca daily bars:

- **Liquidity** — 20-day average dollar volume
- **Volatility opportunity** — ATR as a percentage of price, checked against a target range
- **Risk-adjusted momentum** — 6-month and 12-month momentum, excluding the most recent month
- **Relative strength** — performance vs. `SPY` and `QQQ`
- **Trend quality** — relative position of `close`, `EMA50`, and `EMA200`
- **Optional extended fields** — `roe_ttm`, `debt_to_equity`, `accruals_ratio`, `earnings_revision_1m`, `short_interest_pct_float`, `days_to_cover`

By default it outputs the top `100` symbols by composite score with no forced Long/Short ratio constraint. Use `--long-count` or `--short-count` to impose directional limits.

Use the universe already defined in config:

```bash
python src/universe_optimizer.py --top-k 100 --output-file config/universe_us_top100_optimized.yaml
```

Point to a local symbol list file:

```bash
python src/universe_optimizer.py \
  --input-file config/universe_us_top300.yaml \
  --top-k 100 \
  --output-file config/universe_us_top100_optimized.yaml
```

Fetch a list from a remote YAML / JSON / CSV URL and filter it:

```bash
python src/universe_optimizer.py \
  --input-url https://example.com/my_universe.yaml \
  --input-format auto \
  --output-file config/universe_us_top100_optimized.yaml
```

The output file uses the same `symbols:` structure as the scanner universe files. To use it, point `universe.static_file` in `config/settings.yaml` to the new file.

## Alpaca Notes

- Market data base URL defaults to `https://data.alpaca.markets/v2`
- Trading API defaults to `https://paper-api.alpaca.markets/v2`
- The asset list (used when not in `static_file` mode) is fetched automatically; the client deduplicates any `/v2` suffix.
- Authentication uses the `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` request headers.
- `static_file` / `custom` universe modes work directly. The legacy dynamic Top-N mode falls back to filtering the first N active Alpaca assets (Alpaca does not provide a market-cap ranked asset endpoint like Polygon).
- Default `feed: iex` — the free feed documented by Alpaca. Switch to `sip` in `config/settings.yaml` if you have a SIP subscription.
- Default `adjustment: split` — closest equivalent to Polygon's `adjusted=true` split-adjusted semantics.
- Default active rate limit: `180 req/min` — the Alpaca Basic plan cap is `200/min`; the buffer prevents hitting the limit during pagination and retries.
- Batch bar requests cover all symbols in the universe per timeframe, converging to a small number of paginated requests rather than hundreds of individual symbol calls.
- For paper trading, set `ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets/v2` in `.env`.

## Stop Model

- The **initial stop** for an intraday signal is anchored to the hourly signal bar or the daily swing low/high, establishing the true risk baseline at entry.
- Two initial stop methods are supported: **SafeZone** and **Nick stop**.
  SafeZone uses `EMA22` to measure penetration noise over the last `10` bars. Default multipliers: `2.0` for longs, `3.0` for shorts.
- After entry, the **protective stop** advances using an ATR trailing stop calculated from the latest daily bar's extreme.
  Both `1x ATR` and `2x ATR` levels are calculated; the system defaults to `1x ATR` as the recommended trailing stop.
- End-of-day stop updates apply the one-way rule: long stops only move up, short stops only move down. The initial stop is never overwritten.
- In the Journal, `stop_loss` is the current active stop; `initial_stop_loss` retains the entry-time defensive level.

## Trade Journal Web UI

The project includes a FastAPI + SQLite trade journal served at `frontend/trade_journal/`.

Start the local Journal Server:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export JOURNAL_AUTH_USERNAME=your_username
export JOURNAL_AUTH_PASSWORD=your_password
PYTHONPATH=src .venv/bin/python -m journal
```

Once running, open:

- [http://127.0.0.1:8100/](http://127.0.0.1:8100/)
- [http://127.0.0.1:8100/api/health](http://127.0.0.1:8100/api/health)

When deployed to AWS and accessed via the instance's public IP, no nginx or domain name is required:

- `http://<EC2_PUBLIC_IP>:8100/`
- `http://<EC2_PUBLIC_IP>:8100/api/health`

The frontend and API are served by the same FastAPI process so no CORS configuration is needed.

If `JOURNAL_AUTH_USERNAME` and `JOURNAL_AUTH_PASSWORD` are set, the browser will show an HTTP Basic Auth prompt on page load — sufficient security for personal use over a public IP.

The Journal Server provides:

- Full trade CRUD, persisted to local SQLite
- Risk settings persisted to `trade_settings`
- All pages talk to the local `/api` — no external database connection required

The EOD scan (`--mode eod`) also updates protective stops for all open positions after rebuilding the candidate pool:

- Long stops only move up, never down
- Short stops only move down, never up
- If an open position has an earnings report within the next few days, a reminder is appended to the Telegram summary suggesting early exit or position reduction
- Stop update results are included in the Telegram EOD summary alongside the candidate pool digest

## Scheduling

The recommended approach is to invoke the scanner as a one-shot process via cron or systemd rather than keeping a `--loop` process running permanently:

```bash
0 * * * * cd /path/to/triple_screen && /usr/bin/python3 src/scanner.py --once >> logs/cron.log 2>&1
```

AWS EC2 + systemd deployment templates are in `deploy/aws/README.md`, `deploy/aws/systemd/triple-screen.service`, and `deploy/aws/systemd/triple-screen.timer`.

The timer is pre-configured for US Eastern Time:

- Hourly during market hours Monday–Friday: `09:30` to `15:30`
- Once after each market close: `16:10`

Recommended schedule:

- `16:10` — run `--mode eod` to rebuild the candidate pool for the day
- Next day, hourly during market hours — run `--mode intraday` to scan only the previous session's candidates for triggers
- `--mode auto` runs intraday during market hours and EOD only within the `16:00`–`16:45` close window; it skips all other times automatically

## Implementation Status

- Centralised config, secret isolation, modular package structure, layered scan orchestration, and Telegram / SQLite / Alpaca clients are all complete.
- EOD candidate pool build and strict intraday-only scanning against the previous session's candidates are complete. The system no longer falls back to a full market rebuild when no candidate pool exists.
- Potential future improvements: raw bar incremental cache versioning, per-symbol retry queue, indicator snapshot versioning, additional notification channels.