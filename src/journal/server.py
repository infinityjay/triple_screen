from __future__ import annotations

import base64
import binascii
import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yaml

import trading_models
from config.loader import load_settings
from journal.service import compute_used_stop
from journal.technical_analysis import TechnicalAnalysisError, analyze_symbol
from storage.sqlite import SQLiteStorage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend" / "trade_journal"
load_dotenv(PROJECT_ROOT / ".env", override=False)


class _ServerRuntime:
    def __init__(self, database_path: Path, host: str, port: int) -> None:
        self.database_path = database_path
        self.host = host
        self.port = port
        self.auth_username = (os.getenv("JOURNAL_AUTH_USERNAME") or "").strip()
        self.auth_password = os.getenv("JOURNAL_AUTH_PASSWORD") or ""

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_username and self.auth_password)


def _load_runtime() -> _ServerRuntime:
    try:
        settings = load_settings()
        return _ServerRuntime(
            database_path=settings.storage.database_path,
            host=settings.server.host,
            port=settings.server.port,
        )
    except Exception:
        config_path = PROJECT_ROOT / "config" / "settings.yaml"
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}

        server_raw = raw.get("server", {})
        storage_raw = raw.get("storage", {})
        database_path = Path(storage_raw.get("database_path", "data/triple_screen.db"))
        if not database_path.is_absolute():
            database_path = PROJECT_ROOT / database_path
        return _ServerRuntime(
            database_path=database_path,
            host=server_raw.get("host", "127.0.0.1"),
            port=int(server_raw.get("port", 8100)),
        )


runtime = _load_runtime()
storage = SQLiteStorage(runtime.database_path)
storage.init_db()
FRONTEND_ASSET_CACHE_CONTROL = "no-store, no-cache, must-revalidate, max-age=0"

app = FastAPI(title="Triple Screen Journal API", version="1.0.0")
app.mount("/frontend", StaticFiles(directory=FRONTEND_ROOT), name="frontend")


def _build_basic_auth_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required"},
        headers={"WWW-Authenticate": 'Basic realm="Trading Journal"'},
    )


def _is_authorized(request: Request) -> bool:
    if not runtime.auth_enabled:
        return True

    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False

    token = header.split(" ", 1)[1].strip()
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False

    if ":" not in decoded:
        return False

    username, password = decoded.split(":", 1)
    return secrets.compare_digest(username, runtime.auth_username) and secrets.compare_digest(password, runtime.auth_password)


@app.middleware("http")
async def require_basic_auth(request: Request, call_next):
    if not _is_authorized(request):
        return _build_basic_auth_response()
    response = await call_next(request)
    if request.url.path in {"/", "/journal", "/watchlist", "/analysis"} or request.url.path.startswith("/frontend/"):
        response.headers["Cache-Control"] = FRONTEND_ASSET_CACHE_CONTROL
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class TradePayload(BaseModel):
    stock: str
    direction: str = "long"
    buy_price: float | None = None
    shares: float | None = None
    stop_loss: float | None = None
    initial_stop_loss: float | None = None
    initial_stop_basis: str | None = None
    protective_stop_basis: str | None = None
    stop_reason: str | None = None
    buy_date: str | None = None
    day_high: float | None = None
    day_low: float | None = None
    target_price: float | None = None
    target_pct: float | None = None
    chan_high: float | None = None
    chan_low: float | None = None
    sell_price: float | None = None
    sell_date: str | None = None
    sell_high: float | None = None
    sell_low: float | None = None
    sell_reason: str | None = None
    buy_comm: float | None = None
    sell_comm: float | None = None
    review: str | None = None
    used_stop: float | None = None
    pnl: float | None = None
    pnl_net: float | None = None


class TradeSettingsPayload(BaseModel):
    id: int = 1
    total: float = 0.0
    single_stop: float = 2.0
    month_stop: float = 6.0
    report_month: str | None = None


class SymbolAnalysisPayload(BaseModel):
    symbol: str
    include_ai: bool = True
    model_id: str | None = None


@app.get("/")
def get_index() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "index.html")


@app.get("/journal")
def get_journal_page() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "journal.html")


@app.get("/watchlist")
def get_watchlist_page() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "watchlist.html")


@app.get("/analysis")
def get_analysis_page() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "analysis.html")


@app.get("/api/health")
def get_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "database_path": str(runtime.database_path),
        "server": {"host": runtime.host, "port": runtime.port},
        "auth_enabled": runtime.auth_enabled,
    }


@app.get("/api/trading-models")
def get_trading_models() -> dict[str, Any]:
    try:
        active_model_id = load_settings().trading_model.active
    except Exception:
        active_model_id = trading_models.DEFAULT_MODEL_ID
    return {
        "active_model_id": trading_models.normalize_model_id(active_model_id),
        "models": trading_models.list_models(),
    }


@app.get("/api/trades")
def list_trades() -> list[dict[str, Any]]:
    return storage.list_trades()


@app.post("/api/trades")
def create_trade(payload: TradePayload) -> dict[str, Any]:
    payload_dict = payload.model_dump()
    # Recalculate used_stop to ensure accuracy
    used_stop = compute_used_stop(
        entry_price=payload_dict.get("buy_price"),
        stop_loss=payload_dict.get("stop_loss"),
        shares=payload_dict.get("shares"),
        direction=payload_dict.get("direction"),
    )
    payload_dict["used_stop"] = used_stop
    return storage.insert_trade(payload_dict)


@app.put("/api/trades/{trade_id}")
def update_trade(trade_id: str, payload: TradePayload) -> dict[str, Any]:
    payload_dict = payload.model_dump()
    # Recalculate used_stop to ensure accuracy
    used_stop = compute_used_stop(
        entry_price=payload_dict.get("buy_price"),
        stop_loss=payload_dict.get("stop_loss"),
        shares=payload_dict.get("shares"),
        direction=payload_dict.get("direction"),
    )
    payload_dict["used_stop"] = used_stop
    updated = storage.update_trade(trade_id, payload_dict)
    if not updated:
        raise HTTPException(status_code=404, detail="Trade not found")
    return updated


@app.delete("/api/trades/{trade_id}")
def delete_trade(trade_id: str) -> dict[str, Any]:
    deleted = storage.delete_trade(trade_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"ok": True}


@app.delete("/api/trades")
def clear_trades() -> dict[str, Any]:
    deleted = storage.clear_trades()
    return {"ok": True, "deleted": deleted}


@app.get("/api/settings")
def get_trade_settings() -> dict[str, Any]:
    return storage.get_trade_settings()


@app.put("/api/settings")
def put_trade_settings(payload: TradeSettingsPayload) -> dict[str, Any]:
    return storage.upsert_trade_settings(payload.model_dump())


@app.get("/api/watchlist")
def get_watchlist_data(
    session_date: str | None = Query(default=None),
    session_limit: int = Query(default=8, ge=1, le=30),
) -> dict[str, Any]:
    sessions = storage.list_candidate_sessions(limit=session_limit)
    latest_session = sessions[0]["session_date"] if sessions else None
    target_session = session_date or latest_session
    items = storage.get_qualified_candidates(target_session) if target_session else []

    return {
        "session_date": target_session,
        "latest_session_date": latest_session,
        "available_sessions": sessions,
        "count": len(items),
        "items": items,
    }


@app.post("/api/technical-analysis")
def post_technical_analysis(payload: SymbolAnalysisPayload) -> dict[str, Any]:
    try:
        return analyze_symbol(payload.symbol, include_ai=payload.include_ai, model_id=payload.model_id)
    except TechnicalAnalysisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=runtime.host, port=runtime.port, reload=False)


if __name__ == "__main__":
    run()
