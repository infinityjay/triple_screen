# IBKR Order API Integration — Complete Implementation Plan

## Overview

Integrate the IBKR Client Portal Web API (REST-based, no special Python library) into the existing **"Manual IBKR Order Records"** section of the watchlist page. Users continue to fill the order form as before, but can now tick **"Send to IBKR"** to have the backend automatically resolve the contract ID, place the real order under their IBKR account, and store the returned order ID. Orders can also be status-synced and cancelled directly from the watchlist.

**Deployment context:** The app runs on AWS. The IBKR Client Portal Gateway runs on your **local PC**. A one-command SSH reverse tunnel connects them each trading session.

**API choice rationale:** Client Portal Web API (REST over localhost) is chosen over the TWS socket API because it aligns with the existing `requests`-based `AlpacaClient` pattern and requires no additional Python libraries.

---

## Part 1 — Non-Code: Account & Auth Setup

### 1.1 Account Requirements

- You need a fully open, funded **IBKR Pro** live account (not Lite). Paper trading is available once the live account is active.
- No special API approval is needed for retail/individual clients — free and immediate.

---

### 1.2 One-Time Local Machine Setup

**Step 1 — Install Java 11+**

The CP Gateway requires Java. Verify with `java -version`. Download from https://adoptium.net/ if needed.

**Step 2 — Download the Client Portal Gateway**

Go to: https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/#gw-step-one

Download the zip and extract it. You will get a `root/` folder containing `run.bat` (Windows) / `run.sh` (Linux/Mac) and `conf.yaml`.

**Step 3 — Find your Account ID**

Log in to https://www.interactivebrokers.com/portal/ → **Settings → Account Settings**.

- Live account ID format: `U1234567`
- Paper account ID format: `DU1234567` (recommended for testing first)

---

### 1.3 Each Trading Session — 2 Steps, ~1 Minute

**Step 1 — Start the Gateway on your local PC**

```bat
root\run.bat root\conf.yaml
```

The gateway starts at `https://localhost:5000`.

Open `https://localhost:5000` in your browser. You will see a certificate warning (self-signed) — click **Advanced → Proceed to localhost**. Log in with your IBKR username + password, then approve the 2FA prompt on your IBKR Mobile app. The page will confirm "**Client login succeeds**". Both session tiers (read-only + brokerage/iserver) are now active.

**Step 2 — Open the SSH reverse tunnel to AWS**

```bash
ssh -N -R 5000:localhost:5000 ubuntu@your-aws-ip
```

Keep this terminal open while trading. The AWS server's `https://localhost:5000` now routes through the tunnel to your local gateway.

When done trading, close the terminal. Your journal/scanner continue running normally on AWS.

---

### 1.4 How the Connection Flows

```
Your browser (watchlist page)
        ↓  HTTP
  AWS backend (FastAPI server)
        ↓  https://localhost:5000  (via SSH tunnel)
  Your local PC (CP Gateway)
        ↓  IBKR OAuth session
  IBKR servers
```

---

### 1.5 Session Maintenance (handled by code, nothing to do manually)

| Event | What happens | Handled by |
|---|---|---|
| Every 60 seconds | `/tickle` keepalive call | Server background task |
| Weekday ~01:00 local time | iserver resets briefly | Code calls `/iserver/reauthenticate` |
| Saturday evening maintenance | Full session may drop | Re-authenticate in browser Sunday morning |

---

### 1.6 One-Time AWS Server Setup

Add to `/etc/ssh/sshd_config` (usually already set):
```
AllowTcpForwarding yes
```
Then `sudo systemctl restart sshd`.

Set the environment variable on AWS (in `/etc/environment` or the systemd service file):
```
IBKR_ACCOUNT_ID=U1234567
```

---

## Part 2 — Code Implementation

### Phase 1 — New Backend Client: `src/clients/ibkr.py`

*New file, modeled after `src/clients/alpaca.py`.*

**`IBKRConfig` dataclass fields:**

| Field | Default | Description |
|---|---|---|
| `gateway_url` | `https://localhost:5000` | CP Gateway address |
| `account_id_env` | `"IBKR_ACCOUNT_ID"` | Env var name holding account ID |
| `verify_ssl` | `False` | Gateway uses self-signed cert |
| `timeout_seconds` | `10` | Request timeout |

**`IBKRClient` methods:**

| Method | HTTP call | Purpose |
|---|---|---|
| `check_auth()` | `GET /v1/api/iserver/auth/status` | Returns `{authenticated, connected}` |
| `reauthenticate()` | `GET /v1/api/iserver/reauthenticate` | Restores brokerage session after daily reset |
| `tickle()` | `POST /v1/api/tickle` | Keepalive — called every 60s |
| `search_contract(symbol)` | `GET /v1/api/iserver/secdef/search?symbol=X&secType=STK` | Returns `conid` + description |
| `place_order(acct_id, body)` | `POST /v1/api/iserver/account/{acctId}/orders` | Places order; handles two-step confirmation |
| `_confirm_order_reply(reply_id)` | `POST /v1/api/iserver/reply/{replyId}` `{"confirmed": true}` | Called when IBKR returns a confirmation prompt instead of an order ID |
| `get_order_status(order_id)` | `GET /v1/api/iserver/account/order/status/{orderId}` | Single order status |
| `get_open_orders()` | `GET /v1/api/iserver/account/orders` | All live orders this session |
| `cancel_order(acct_id, order_id)` | `DELETE /v1/api/iserver/account/{acctId}/order/{orderId}` | Cancel by order ID |

**Order type mapping (form value → IBKR `orderType` + price fields):**

| Form value | IBKR `orderType` | `price` field | `auxPrice` field |
|---|---|---|---|
| Stop Limit | `STP LMT` | `limit_price` | `stop_price` (trigger) |
| Limit | `LMT` | `limit_price` | — |
| Stop | `STP` | — | `stop_price` (trigger) |

**Direction mapping:** `LONG` → `BUY`, `SHORT` → `SELL`

**Order request body shape sent to IBKR:**

```json
{
  "conid": 265598,
  "orderType": "STP LMT",
  "side": "BUY",
  "quantity": 100,
  "price": 185.50,
  "auxPrice": 185.00,
  "tif": "DAY"
}
```

**Two-step confirmation handling:** If the first `POST /orders` response contains an `"id"` field (order reply message) instead of `"order_id"`, the client automatically calls `POST /iserver/reply/{id}` with `{"confirmed": true}` and returns the resulting `order_id`.

---

### Phase 2 — Config: `config/settings.yaml`

Add an `ibkr:` block parallel to the existing `alpaca:` block under `data_source:`:

```yaml
ibkr:
  gateway_url: "https://localhost:5000"
  account_id_env: "IBKR_ACCOUNT_ID"
  verify_ssl: false
  timeout_seconds: 10
```

---

### Phase 3 — Config Schema & Loader

**`src/config/schema.py`:**
- Add `IBKRConfig` dataclass with the four fields from Phase 2
- Add `ibkr: IBKRConfig | None = None` to the existing `AppConfig` dataclass

**`src/config/loader.py`:**
- Parse the optional `ibkr:` YAML block into `IBKRConfig`
- If block is absent, `app_config.ibkr` remains `None` and all IBKR features are disabled gracefully

*Parallel with Phase 2, no dependencies.*

---

### Phase 4 — New Server Endpoints: `src/journal/server.py`

Five new endpoints under `/api/ibkr/`. `IBKRClient` is instantiated once at server startup (if `config.ibkr` is not None) and injected. All endpoints return `503` with `{"error": "IBKR not configured"}` if the config block is absent.

**Background keepalive task** (add to server startup): calls `ibkr_client.tickle()` every 55 seconds and `ibkr_client.reauthenticate()` once after each daily maintenance window (~01:05 local time).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/ibkr/status` | Proxy `check_auth()` → `{authenticated, connected, gateway_url}`. Drives the UI status dot. |
| `POST` | `/api/ibkr/search-contract` | Body `{symbol}` → `{conid, description}`. Used by the form before placing. |
| `POST` | `/api/ibkr/place-order` | Body = `PlannedOrderPayload` fields. Places order, then auto-upserts `planned_orders` row with `broker_order_id` + `ibkr_conid`. Returns `{ibkr_order_id, status}`. |
| `GET` | `/api/ibkr/orders` | Proxy `get_open_orders()` → list of live IBKR orders. |
| `DELETE` | `/api/ibkr/orders/{order_id}` | Cancel IBKR order, update local `planned_orders` status to `CANCELLED`. |

*Depends on Phase 1 and Phase 3.*

---

### Phase 5 — DB Migration: `src/storage/sqlite.py`

Add `ibkr_conid TEXT` column to `planned_orders` using the existing `_ensure_column` migration pattern (already used for `stop_loss`). This caches the resolved contract ID so status-refresh calls don't need to re-search by symbol.

*Independent, can run in parallel with Phase 4.*

---

### Phase 6 — UI Changes

#### `frontend/trade_journal/watchlist.html`

1. **Gateway status badge** in the `IBKR Manual Orders` card header:
   - A small colored dot (`●`) + text label ("Connected" / "Disconnected")
   - `id="ibkrStatusDot"` — toggled by JS on page load

2. **"Send to IBKR" checkbox** inside `#plannedOrderForm`, alongside the existing Save button:
   ```html
   <label class="checkbox-label">
     <input type="checkbox" id="sendToIbkr"> Send to IBKR
   </label>
   ```

3. **Hidden `ibkr_conid` input** in the form to carry the resolved contract ID through the flow.

4. **Planned orders table enhancements** — add three new columns per row:
   - IBKR status badge (shows `broker_order_id` or "Local only")
   - Refresh icon button (syncs status from IBKR live)
   - Cancel link (only shown for submitted/presubmitted orders)

#### `frontend/trade_journal/assets/scripts/watchlist-page.js`

1. **On page load**: `GET /api/ibkr/status` → set `#ibkrStatusDot` color and label.

2. **Form submit handler** — new branch when `#sendToIbkr` is checked:
   - `POST /api/ibkr/search-contract` `{symbol}` → get `conid`, store in hidden field
   - `POST /api/ibkr/place-order` with full form data → get `ibkr_order_id`
   - Auto-fill `#orderBrokerId` with the returned order ID
   - Set `#orderStatus` to `PRESUBMITTED`
   - Fall through to existing save logic (which POSTs to `/api/planned-orders` as normal)
   - Show inline success/error message if IBKR placement fails, still allow saving as local-only record

3. **Per-row Refresh button**: `GET /api/ibkr/orders`, find matching `orderId`, update status badge in row and PATCH local record status.

4. **Per-row Cancel button**: `DELETE /api/ibkr/orders/{orderId}` → on success, update local row status to `CANCELLED`.

*Depends on Phase 4.*

---

### Phase 7 — `requirements.txt`

No new packages required. The `requests` library already present is sufficient for all CP Gateway HTTP calls. `verify=False` is used for the self-signed cert; add `urllib3` warning suppression in `ibkr.py`:

```python
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
```

---

## Relevant Files

| File | Action | Notes |
|---|---|---|
| `src/clients/ibkr.py` | **Create new** | Full IBKRClient + IBKRConfig |
| `src/clients/alpaca.py` | Reference only | Pattern to follow |
| `config/settings.yaml` | Modify | Add `ibkr:` block under `data_source:` |
| `src/config/schema.py` | Modify | Add `IBKRConfig` dataclass |
| `src/config/loader.py` | Modify | Parse optional `ibkr:` block |
| `src/journal/server.py` | Modify | Add 5 endpoints + keepalive task |
| `src/journal/service.py` | Modify | Wire `IBKRClient` at startup |
| `src/storage/sqlite.py` | Modify | `_ensure_column` for `ibkr_conid` |
| `frontend/trade_journal/watchlist.html` | Modify | Status dot, checkbox, table columns |
| `frontend/trade_journal/assets/scripts/watchlist-page.js` | Modify | Status poll, place-order flow, refresh/cancel |

---

## Execution Order & Parallelism

```
Phase 2 (yaml)  ──┐
Phase 3 (schema)──┼──► Phase 4 (endpoints) ──► Phase 6 (UI)
Phase 1 (client)──┘
Phase 5 (DB)    ──────────────────────────────► (independent)
```

Phases 1, 2, 3, and 5 can all be done in parallel. Phase 4 depends on 1+3. Phase 6 depends on 4.

---

## Verification Checklist

1. Start CP Gateway locally + authenticate in browser → `https://localhost:5000` shows "Client login succeeds"
2. Open SSH tunnel: `ssh -N -R 5000:localhost:5000 ubuntu@your-aws-ip`
3. Call `GET /api/ibkr/status` from the watchlist page → green dot appears
4. Fill the order form for a **paper account** symbol, check "Send to IBKR", submit → confirm order appears in IBKR TWS/paper account
5. Verify `planned_orders` DB row has `broker_order_id` and `ibkr_conid` populated
6. Click "Refresh" on the row → status updates from IBKR live
7. Click "Cancel" → order disappears from IBKR open orders, row shows `CANCELLED`
8. Close the SSH tunnel → `GET /api/ibkr/status` returns red dot; journal/scanner continue working normally

---

## Scope Boundaries

**Included:**
- Place orders (Stop Limit, Limit, Stop) for US equities
- Auto-save broker order ID to local DB
- Status refresh per order
- Cancel orders

**Explicitly excluded:**
- Order modification (price/qty edit after placement)
- Bracket orders / attached stop-loss orders
- Multi-account selection (single account via env var)
- Market orders
- Options or non-equity instruments
