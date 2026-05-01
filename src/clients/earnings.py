from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, date, datetime, timedelta

import requests

from config.schema import EarningsCalendarConfig
from storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EarningsCalendarClient:
    def __init__(self, config: EarningsCalendarConfig, storage: SQLiteStorage | None = None) -> None:
        self.config = config
        self.storage = storage

    def _request_calendar(self) -> str | None:
        if not self.config.enabled or self.config.provider != "alphavantage" or not self.config.api_key:
            return None

        try:
            response = requests.get(
                self.config.base_url,
                params={
                    "function": "EARNINGS_CALENDAR",
                    "horizon": self.config.horizon,
                    "apikey": self.config.api_key,
                },
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            return response.text
        except Exception as exc:
            logger.warning("Failed to fetch earnings calendar: %s", exc)
            return None

    def _load_from_cache(self, symbols: list[str]) -> dict[str, dict]:
        if not self.storage:
            return {}

        events: dict[str, dict] = {}
        for symbol in symbols:
            row = self.storage.get_earnings_event(symbol)
            if not row:
                continue
            events[symbol] = dict(row)
        return events

    @staticmethod
    def _has_future_report_date(event: dict, session_date: date) -> bool:
        report_date = event.get("report_date")
        if not report_date:
            return False
        try:
            return datetime.fromisoformat(str(report_date)).date() >= session_date
        except ValueError:
            return False

    def get_upcoming_earnings(self, symbols: list[str], session_date: date | None = None) -> dict[str, dict]:
        if not symbols:
            return {}
        unique_symbols = sorted(set(symbols))
        effective_session_date = session_date or _utc_now().date()

        if not self.config.enabled:
            return {}

        cached = self._load_from_cache(unique_symbols)
        cached_with_future_event = {
            symbol: event
            for symbol, event in cached.items()
            if self._has_future_report_date(event, effective_session_date)
        }
        if len(cached_with_future_event) == len(unique_symbols):
            logger.info(
                "Using cached future earnings dates for %s symbols; skipping Alpha Vantage request.",
                len(unique_symbols),
            )
            return cached_with_future_event

        if self.storage:
            updated_at = self.storage.get_latest_earnings_update_time()
            if updated_at and _utc_now() - updated_at <= timedelta(hours=12):
                if len(cached_with_future_event) == len(unique_symbols):
                    return cached_with_future_event

        payload = self._request_calendar()
        if not payload:
            return cached_with_future_event

        reader = csv.DictReader(io.StringIO(payload))
        events_by_symbol: dict[str, dict] = {}
        records: list[dict] = []
        for row in reader:
            symbol = (row.get("symbol") or "").strip().upper()
            if symbol not in unique_symbols:
                continue
            report_date = (row.get("reportDate") or row.get("report_date") or "").strip()
            if not report_date:
                continue
            event = {
                "symbol": symbol,
                "report_date": report_date,
                "fiscal_date_ending": (row.get("fiscalDateEnding") or row.get("fiscal_date_ending") or "").strip() or None,
                "estimate": (row.get("estimate") or "").strip() or None,
            }
            existing = events_by_symbol.get(symbol)
            if existing is None or report_date < existing["report_date"]:
                events_by_symbol[symbol] = event

        records.extend(events_by_symbol.values())
        if self.storage and records:
            self.storage.upsert_earnings_events(records)

        merged = self._load_from_cache(unique_symbols)
        merged.update(events_by_symbol)
        return {
            symbol: event
            for symbol, event in merged.items()
            if self._has_future_report_date(event, effective_session_date)
        }
