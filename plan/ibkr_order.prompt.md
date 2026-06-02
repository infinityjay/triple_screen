# IBKR Order API Integration — Complete Implementation Plan

## Overview

Integrate the IBKR Client Portal Web API (REST-based, no special Python library) into the existing **"Manual IBKR Order Records"** section of the watchlist page. Users continue to fill the order form as before, but can now tick **"Send to IBKR"** to have the backend automatically resolve the contract ID, place the real order under their IBKR account, and store the returned order ID. Orders can also be status-synced and cancelled directly from the watchlist.

**Deployment context:** The app runs on AWS. The IBKR Client Portal Gateway also runs on **AWS** as a systemd service, exposed via nginx with HTTPS and a Let's Encrypt certificate. Authentication is completed once per session from any browser — including iPhone — with no local machine or SSH tunnel required.

**API choice rationale:** Client Portal Web API (REST over localhost) is chosen over the TWS socket API because it aligns with the existing `requests`-based `AlpacaClient` pattern and requires no additional Python libraries.

---

## Part 1 — Non-Code: Account & Auth Setup

### 1.1 Account Requirements

- You need a fully open, funded **IBKR Pro** live account (not Lite). Paper trading is available once the live account is active.
- No special API approval is needed for retail/individual clients — free and immediate.

---

### 1.2 One-Time AWS Setup

**Step 1 — Install Java 17 on AWS**

```bash
sudo apt update && sudo apt install -y openjdk-17-jre-headless
java -version
```

**Step 2 — Download and Install the CP Gateway**

Download the zip from:
https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/#gw-step-one

Upload it to AWS (e.g. via `scp`) and extract:

```bash
mkdir -p /opt/ibkr-gateway && cd /opt/ibkr-gateway
unzip clientportal.gw.zip -d .
chmod +x root/run.sh
```

In `root/conf.yaml`, confirm `listenPort: 5000` and `listenSsl: true`.

**Step 3 — Create a systemd Service**

Create `/etc/systemd/system/ibkr-gateway.service`:

```ini
[Unit]
Description=IBKR Client Portal Gateway
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/ibkr-gateway
ExecStart=/bin/bash /opt/ibkr-gateway/root/run.sh /opt/ibkr-gateway/root/conf.yaml
Restart=on-failure
RestartSec=10
Environment=JAVA_OPTS=-Xmx256m
Environment=IBKR_ACCOUNT_ID=U1234567

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ibkr-gateway
sudo systemctl start ibkr-gateway
```

**Step 4 — Install nginx and Get a TLS Certificate**

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d your-aws-domain.com
```

Create `/etc/nginx/sites-available/ibkr-gateway`:

```nginx
server {
    listen 443 ssl;
    server_name your-aws-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-aws-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-aws-domain.com/privkey.pem;

    # Option A — IP allowlist (recommended: your home + mobile IPs only)
    # allow 1.2.3.4;   # home IP
    # deny all;

    # Option B — HTTP basic auth (if your IP changes frequently)
    auth_basic "IBKR Gateway";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass         https://localhost:5000;
        proxy_ssl_verify   off;   # Gateway uses a self-signed cert
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}

server {
    listen 80;
    server_name your-aws-domain.com;
    return 301 https://$host$request_uri;
}
```

```bash
# If using basic auth, create the password file:
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd ibkr   # enter a strong password

sudo ln -s /etc/nginx/sites-available/ibkr-gateway /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**Step 5 — Open Port 443 in AWS Security Group**

EC2 → Security Groups → your instance → Inbound rules → Add:

- Type: **HTTPS**, Port: **443**, Source: your home IP (`x.x.x.x/32`), or `0.0.0.0/0` if relying on basic auth

**Step 6 — Find Your Account ID**

Log in to https://www.interactivebrokers.com/portal/ → **Settings → Account Settings**.

- Live account ID format: `U1234567`
- Paper account ID format: `DU1234567` (recommended for testing first)

---

### 1.3 Each Trading Session — ~30 Seconds

Open `https://your-aws-domain.com` in any browser — desktop, iPhone, or Android. If using basic auth (Option B), enter your nginx credentials first.

Log in with your IBKR username and password, then approve the 2FA prompt on your IBKR Mobile app. The page will confirm "**Client login succeeds**". Both session tiers are now active.

No local machine, no SSH terminal, no Java installation needed.

---

### 1.4 How the Connection Flows

```
Your browser / iPhone
        ↓  HTTPS port 443  (Let's Encrypt cert)
  AWS nginx  (IP allowlist or basic auth)
        ↓  https://localhost:5000
  CP Gateway  (AWS systemd service, self-signed cert)
        ↓  IBKR OAuth session
  IBKR servers

AWS backend (FastAPI)  ← same host, no tunnel
        ↓  https://localhost:5000  (direct call)
  CP Gateway
```

---

### 1.5 Session Maintenance (handled by code, nothing to do manually)

| Event                        | What happens             | Handled by                                |
| ---------------------------- | ------------------------ | ----------------------------------------- |
| Every 60 seconds             | `/tickle` keepalive call | Server background task                    |
| Weekday ~01:00 local time    | iserver resets briefly   | Code calls `/iserver/reauthenticate`      |
| Saturday evening maintenance | Full session may drop    | Re-authenticate in browser Sunday morning |

---

## Part 2 — Code Implementation

### Phase 1 — New Backend Client: `src/clients/ibkr.py`

_New file, modeled after `src/clients/alpaca.py`._

**`IBKRConfig` dataclass fields:**

| Field             | Default                  | Description                     |
| ----------------- | ------------------------ | ------------------------------- |
| `gateway_url`     | `https://localhost:5000` | CP Gateway address              |
| `account_id_env`  | `"IBKR_ACCOUNT_ID"`      | Env var name holding account ID |
| `verify_ssl`      | `False`                  | Gateway uses self-signed cert   |
| `timeout_seconds` | `10`                     | Request timeout                 |

**`IBKRClient` methods:**

| Method                            | HTTP call                                                    | Purpose                                                               |
| --------------------------------- | ------------------------------------------------------------ | --------------------------------------------------------------------- |
| `check_auth()`                    | `GET /v1/api/iserver/auth/status`                            | Returns `{authenticated, connected}`                                  |
| `reauthenticate()`                | `GET /v1/api/iserver/reauthenticate`                         | Restores brokerage session after daily reset                          |
| `tickle()`                        | `POST /v1/api/tickle`                                        | Keepalive — called every 60s                                          |
| `search_contract(symbol)`         | `GET /v1/api/iserver/secdef/search?symbol=X&secType=STK`     | Returns `conid` + description                                         |
| `place_order(acct_id, body)`      | `POST /v1/api/iserver/account/{acctId}/orders`               | Places order; handles two-step confirmation                           |
| `_confirm_order_reply(reply_id)`  | `POST /v1/api/iserver/reply/{replyId}` `{"confirmed": true}` | Called when IBKR returns a confirmation prompt instead of an order ID |
| `get_order_status(order_id)`      | `GET /v1/api/iserver/account/order/status/{orderId}`         | Single order status                                                   |
| `get_open_orders()`               | `GET /v1/api/iserver/account/orders`                         | All live orders this session                                          |
| `cancel_order(acct_id, order_id)` | `DELETE /v1/api/iserver/account/{acctId}/order/{orderId}`    | Cancel by order ID                                                    |

**Order type mapping (form value → IBKR `orderType` + price fields):**

| Form value | IBKR `orderType` | `price` field | `auxPrice` field       |
| ---------- | ---------------- | ------------- | ---------------------- |
| Stop Limit | `STP LMT`        | `limit_price` | `stop_price` (trigger) |
| Limit      | `LMT`            | `limit_price` | —                      |
| Stop       | `STP`            | —             | `stop_price` (trigger) |

**Direction mapping:** `LONG` → `BUY`, `SHORT` → `SELL`

**Order request body shape sent to IBKR:**

```json
{
  "conid": 265598,
  "orderType": "STP LMT",
  "side": "BUY",
  "quantity": 100,
  "price": 185.5,
  "auxPrice": 185.0,
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

_Parallel with Phase 2, no dependencies._

---

### Phase 4 — New Server Endpoints: `src/journal/server.py`

Five new endpoints under `/api/ibkr/`. `IBKRClient` is instantiated once at server startup (if `config.ibkr` is not None) and injected. All endpoints return `503` with `{"error": "IBKR not configured"}` if the config block is absent.

**Background keepalive task** (add to server startup): calls `ibkr_client.tickle()` every 55 seconds and `ibkr_client.reauthenticate()` once after each daily maintenance window (~01:05 local time).

| Method   | Path                          | Description                                                                                                                                                         |
| -------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/api/ibkr/status`            | Proxy `check_auth()` → `{authenticated, connected, gateway_url}`. Drives the UI status dot.                                                                         |
| `POST`   | `/api/ibkr/search-contract`   | Body `{symbol}` → `{conid, description}`. Used by the form before placing.                                                                                          |
| `POST`   | `/api/ibkr/place-order`       | Body = `PlannedOrderPayload` fields. Places order, then auto-upserts `planned_orders` row with `broker_order_id` + `ibkr_conid`. Returns `{ibkr_order_id, status}`. |
| `GET`    | `/api/ibkr/orders`            | Proxy `get_open_orders()` → list of live IBKR orders.                                                                                                               |
| `DELETE` | `/api/ibkr/orders/{order_id}` | Cancel IBKR order, update local `planned_orders` status to `CANCELLED`.                                                                                             |

_Depends on Phase 1 and Phase 3._

---

### Phase 5 — DB Migration: `src/storage/sqlite.py`

Add `ibkr_conid TEXT` column to `planned_orders` using the existing `_ensure_column` migration pattern (already used for `stop_loss`). This caches the resolved contract ID so status-refresh calls don't need to re-search by symbol.

_Independent, can run in parallel with Phase 4._

---

### Phase 6 — UI Changes

#### `frontend/trade_journal/watchlist.html`

1. **Gateway status badge** in the `IBKR Manual Orders` card header:
   - A small colored dot (`●`) + text label ("Connected" / "Disconnected")
   - `id="ibkrStatusDot"` — toggled by JS on page load

2. **"Send to IBKR" checkbox** inside `#plannedOrderForm`, alongside the existing Save button:

   ```html
   <label class="checkbox-label">
     <input type="checkbox" id="sendToIbkr" /> Send to IBKR
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

_Depends on Phase 4._

---

### Phase 7 — `requirements.txt`

No new packages required. The `requests` library already present is sufficient for all CP Gateway HTTP calls. `verify=False` is used for the self-signed cert; add `urllib3` warning suppression in `ibkr.py`:

```python
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
```

---

## Relevant Files

| File                                                      | Action         | Notes                                         |
| --------------------------------------------------------- | -------------- | --------------------------------------------- |
| `src/clients/ibkr.py`                                     | **Create new** | Full IBKRClient + IBKRConfig                  |
| `src/clients/alpaca.py`                                   | Reference only | Pattern to follow                             |
| `config/settings.yaml`                                    | Modify         | Add `ibkr:` block under `data_source:`        |
| `src/config/schema.py`                                    | Modify         | Add `IBKRConfig` dataclass                    |
| `src/config/loader.py`                                    | Modify         | Parse optional `ibkr:` block                  |
| `src/journal/server.py`                                   | Modify         | Add 5 endpoints + keepalive task              |
| `src/journal/service.py`                                  | Modify         | Wire `IBKRClient` at startup                  |
| `src/storage/sqlite.py`                                   | Modify         | `_ensure_column` for `ibkr_conid`             |
| `frontend/trade_journal/watchlist.html`                   | Modify         | Status dot, checkbox, table columns           |
| `frontend/trade_journal/assets/scripts/watchlist-page.js` | Modify         | Status poll, place-order flow, refresh/cancel |

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

1. Authenticate via `https://your-aws-domain.com` → shows "Client login succeeds" (works from iPhone)
2. Call `GET /api/ibkr/status` from the watchlist page → green dot appears
3. Fill the order form for a **paper account** symbol, check "Send to IBKR", submit → confirm order appears in IBKR TWS/paper account
4. Verify `planned_orders` DB row has `broker_order_id` and `ibkr_conid` populated
5. Click "Refresh" on the row → status updates from IBKR live
6. Click "Cancel" → order disappears from IBKR open orders, row shows `CANCELLED`
7. `sudo systemctl stop ibkr-gateway` on AWS → `GET /api/ibkr/status` returns red dot; journal/scanner continue working normally

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
