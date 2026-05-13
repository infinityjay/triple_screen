from __future__ import annotations

import logging
from datetime import UTC, datetime
from html import escape
from typing import Any

import requests

from config.schema import TelegramConfig

logger = logging.getLogger(__name__)


def _html_text(value: Any, default: str = "—") -> str:
    if value is None:
        return default
    return escape(str(value), quote=False)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_clock_label() -> str:
    return _utc_now().strftime("%H:%M UTC")


def _utc_datetime_label() -> str:
    return _utc_now().strftime("%Y-%m-%d %H:%M UTC")


class TelegramNotifier:
    def __init__(self, settings: TelegramConfig) -> None:
        self.settings = settings
        self.base_url = (
            f"https://api.telegram.org/bot{settings.bot_token}"
            if settings.enabled and settings.bot_token
            else None
        )

    def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.settings.enabled or not self.base_url or not self.settings.chat_id:
            return False

        for attempt in range(3):
            try:
                response = requests.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.settings.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                    timeout=15,
                )
                if response.status_code == 200:
                    return True
                logger.warning("Telegram send failed %s: %s", response.status_code, response.text)
            except Exception as exc:
                logger.warning("Telegram send exception (%s/3): %s", attempt + 1, exc)
        return False

    @staticmethod
    def _bar(value: float, max_value: float, length: int = 10, fill: str = "█", empty: str = "░") -> str:
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        pct = min(value / max_value, 1.0) if max_value > 0 else 0
        filled = int(pct * length)
        return fill * filled + empty * (length - filled)

    @staticmethod
    def _score_stars(score: float) -> str:
        stars = int(score / 2)
        return "⭐" * stars + "☆" * (5 - stars)

    @staticmethod
    def _bool_text(value: Any) -> str:
        return "Yes" if bool(value) else "No"

    @staticmethod
    def _status_label(signal: dict) -> str:
        if signal.get("opportunity_status") == "TRIGGERED":
            return "Triggered"
        if signal.get("opportunity_status") == "TOUCHED_ENTRY_PRICE":
            return "Touched entry level"
        if signal.get("opportunity_status") == "MONITOR":
            return "Monitoring"
        return "Watching"

    @staticmethod
    def _daily_state_label(state: str) -> str:
        labels = {
            "RECOVERING": "Recovering from oversold",
            "ROLLING_OVER": "Rolling over from overbought",
            "OVERSOLD": "Oversold",
            "OVERBOUGHT": "Overbought",
            "OVERSOLD_WAIT": "Oversold, waiting for turn",
            "OVERBOUGHT_WAIT": "Overbought, waiting for turn",
            "POST_OVERSOLD_WATCH": "Post-oversold watch",
            "POST_OVERBOUGHT_WATCH": "Post-overbought watch",
            "PULLBACK_WATCH": "Pullback watch",
            "RALLY_WATCH": "Rally watch",
            "NO_PULLBACK": "No clean pullback",
            "NO_RALLY": "No clean rally",
            "STRUCTURE_BROKEN": "Structure broken",
            "ACCELERATING_PULLBACK": "Pullback still accelerating",
            "ACCELERATING_RALLY": "Rally still accelerating",
            "PULLBACK_WAIT_VALUE_BAND": "Waiting for 13EMA value band",
            "RALLY_WAIT_VALUE_BAND": "Waiting for 13EMA value band",
            "PULLBACK_WAIT_HISTOGRAM": "In value band, waiting for histogram turn up",
            "RALLY_WAIT_HISTOGRAM": "In value band, waiting for histogram turn down",
            "PULLBACK_HISTOGRAM_TURNED": "Histogram turned up in value band",
            "RALLY_HISTOGRAM_TURNED": "Histogram turned down in value band",
            "NEUTRAL": "Neutral",
            "PULLBACK_WAIT_FORCE_BELOW_ZERO": "Waiting for Force below zero",
            "PULLBACK_FORCE_READY_WAIT_IMPULSE": "Force ready, waiting for daily impulse",
            "PULLBACK_FORCE_BELOW_ZERO": "Force below zero",
            "RALLY_WAIT_FORCE_ABOVE_ZERO": "Waiting for Force above zero",
            "RALLY_FORCE_READY_WAIT_IMPULSE": "Force ready, waiting for daily impulse",
            "RALLY_FORCE_ABOVE_ZERO": "Force above zero",
        }
        return labels.get(state, state)

    @staticmethod
    def _hourly_status_label(status: str) -> str:
        labels = {
            "TRIGGERED": "Triggered",
            "WAITING_BREAKOUT": "Waiting for upside break",
            "WAITING_BREAKDOWN": "Waiting for downside break",
            "WAITING_ENTRY_PRICE": "Waiting for entry level",
            "TOUCHED_ENTRY_PRICE": "Touched, waiting for hourly confirmation",
            "WAITING_NEXT_BAR": "Waiting for next hourly bar",
            "NEUTRAL": "Neutral",
        }
        return labels.get(status, status)

    @staticmethod
    def _stop_basis_label(stop_basis: str) -> str:
        labels = {
            "SAFEZONE": "SafeZone initial stop",
            "SIGNAL_BAR": "Hourly signal-bar stop",
            "NICK": "Nick stop",
            "ATR_1X": "ATR trailing stop 1x",
            "ATR_2X": "ATR trailing stop 2x",
            "CHOICE_REQUIRED": "Manual choice required",
            "PULLBACK_PIVOT": "Daily pullback pivot stop",
            "MANUAL": "Manual stop",
            "UNKNOWN": "Protective stop",
        }
        return labels.get(stop_basis, stop_basis)

    @staticmethod
    def _candidate_score(signal: dict) -> float:
        return float(signal.get("candidate_score", signal.get("signal_score", 0.0)) or 0.0)

    @staticmethod
    def _execution_score(signal: dict) -> float:
        return float(signal.get("execution_score", signal.get("signal_score", 0.0)) or 0.0)

    @staticmethod
    def _fmt_num(value: Any, digits: int = 2, signed: bool = False) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        return f"{number:+.{digits}f}" if signed else f"{number:.{digits}f}"

    def _format_stop_methods(self, methods: list[dict[str, Any]] | None) -> str:
        if not methods:
            return "No stop-method details"

        initial_methods = [method for method in methods if method.get("group") == "initial"]
        trailing_methods = [method for method in methods if method.get("group") == "trailing"]

        def build_lines(title: str, items: list[dict[str, Any]]) -> list[str]:
            if not items:
                return [f"{title}: none"]
            lines = [title]
            for method in items[:4]:
                price = "Manual review" if method.get("price") is None else self._fmt_num(method.get("price"), 2)
                reference = f"; reference {method.get('reference')}" if method.get("reference") else ""
                lines.append(
                    f"• {method.get('label', 'Stop method')}: <code>{price}</code>{reference} {method.get('suitable_for', '')}"
                )
            return lines

        return "\n".join(build_lines("Initial stops", initial_methods) + build_lines("Trailing stops", trailing_methods))

    def _entry_option_label(self, option: dict[str, Any]) -> str:
        label = option.get("label") or option.get("code") or "Entry reference"
        label = {
            "EMA\u7a7f\u900f\u53c2\u8003\u4ef7": "EMA penetration reference",
            "\u524d\u65e5\u7a81\u7834\u53c2\u8003\u4ef7": "Previous-day breakout reference",
        }.get(str(label), str(label))
        status = "Triggered" if option.get("triggered") else "Not triggered"
        return f"{label} ({status})"

    def _format_entry_options(self, options: list[dict] | None) -> str:
        if not options:
            return ""
        lines: list[str] = []
        for option in options:
            exits = option.get("exits") or {}
            label = _html_text(self._entry_option_label(option))
            lines.append(
                f"• {label}: Entry <code>{self._fmt_num(option.get('price'), 2)}</code>  "
                f"Model stop <code>{self._fmt_num(exits.get('initial_stop_model_loss'), 2)}</code>  "
                f"Protective stop <code>{self._fmt_num(exits.get('protective_stop_loss'), 2)}</code>  "
                f"Target <code>{self._fmt_num(exits.get('take_profit'), 2)}</code>  "
                f"RR <code>{self._fmt_num(exits.get('reward_risk_ratio_model'), 2)}R</code>"
            )
        return "\n".join(lines)

    def _format_entry_options_summary(self, options: list[dict] | None) -> str:
        if not options:
            return ""
        parts: list[str] = []
        for option in options:
            exits = option.get("exits") or {}
            label = option.get("label") or option.get("code") or "Entry"
            label = {
                "EMA\u7a7f\u900f\u53c2\u8003\u4ef7": "EMA penetration reference",
                "\u524d\u65e5\u7a81\u7834\u53c2\u8003\u4ef7": "Previous-day breakout reference",
            }.get(str(label), str(label))
            status = "Triggered" if option.get("triggered") else "Not triggered"
            parts.append(
                f"{label}({status}) {self._fmt_num(option.get('price'), 2)} / "
                f"Stop {self._fmt_num(exits.get('initial_stop_model_loss'), 2)} / "
                f"Target {self._fmt_num(exits.get('take_profit'), 2)} / "
                f"RR {self._fmt_num(exits.get('reward_risk_ratio_model'), 2)}R"
            )
        return "；".join(parts)

    def _entry_option_by_code(self, options: list[dict] | None, code: str) -> dict[str, Any]:
        if not options:
            return {}
        return next((option for option in options if option.get("code") == code), {})

    def _format_entry_option_price(self, option: dict[str, Any]) -> str:
        if not option:
            return "—"
        triggered = "*" if option.get("triggered") else ""
        return f"{self._fmt_num(option.get('price'), 2)}{triggered}"

    def _format_trigger_reason(self, signal: dict[str, Any]) -> str:
        hourly = signal.get("hourly") or {}
        labels = {
            "EMA_PENETRATION": "EMA penetration",
            "PREVIOUS_DAY_BREAK": "Previous-day break",
        }
        trigger_sources = hourly.get("trigger_sources") or []
        if trigger_sources:
            return "+".join(labels.get(source, str(source)) for source in trigger_sources)
        trigger_source = hourly.get("trigger_source")
        if trigger_source:
            return labels.get(trigger_source, str(trigger_source))
        return str(hourly.get("reason") or "Trigger")

    def format_signal_message(self, signal: dict) -> str:
        direction = signal["direction"]
        symbol = signal["symbol"]
        score = self._execution_score(signal)
        rank = signal.get("rank")
        total_ranked = signal.get("total_ranked")
        rank_group = signal.get("rank_group")
        weekly = signal["weekly"]
        daily = signal["daily"]
        hourly = signal["hourly"]
        exits = signal["exits"]
        earnings = signal.get("earnings", {})
        divergence = signal.get("divergence", {})
        entry_plan = hourly.get("entry_plan") or daily.get("entry_plan") or {}
        entry_options = hourly.get("entry_options") or []
        weekly_target = exits.get("weekly_value_target") or weekly.get("weekly_value_target") or {}
        entry_options_block = self._format_entry_options(entry_options) or (
            f"Entry: <code>{self._fmt_num(exits.get('entry'), 2)}</code>"
        )

        dir_emoji = "🚀" if direction == "LONG" else "🔻"
        dir_label = "Long" if direction == "LONG" else "Short"
        daily_state_label = self._daily_state_label(daily["rsi_state"])
        hourly_status_label = self._hourly_status_label(hourly["status"])
        stop_basis_label = self._stop_basis_label(exits.get("stop_basis", "UNKNOWN"))
        initial_stop_basis_label = self._stop_basis_label(exits.get("initial_stop_basis", exits.get("stop_basis", "UNKNOWN")))
        protective_stop_basis_label = self._stop_basis_label(exits.get("protective_stop_basis", "ATR_1X"))
        breakout_bar = self._bar(hourly.get("breakout_strength", 0), 1.0, length=8)
        entry_price = self._fmt_num(hourly.get("entry_price"), 2)
        hourly_close = self._fmt_num(hourly.get("close"), 2)
        current_high = self._fmt_num(hourly.get("current_high"), 2)
        current_low = self._fmt_num(hourly.get("current_low"), 2)
        signal_bar_high = self._fmt_num(hourly.get("signal_bar_high"), 2)
        signal_bar_low = self._fmt_num(hourly.get("signal_bar_low"), 2)
        trigger_source = hourly.get("trigger_source")

        if direction == "LONG":
            if signal.get("opportunity_status") == "TRIGGERED":
                if trigger_source in {"EMA_PENETRATION", "PREVIOUS_DAY_BREAK"}:
                    breakout_line = f"Trigger source: {_html_text(trigger_source)}  Current: {hourly_close}  Trigger: {entry_price}"
                else:
                    breakout_line = f"Previous closed hourly high: {signal_bar_high}  Current: {hourly_close}  Trigger: {entry_price}"
            else:
                breakout_line = f"Current tracked stop: {entry_price}  Current hourly high: {current_high}  Current: {hourly_close}"
        else:
            if signal.get("opportunity_status") == "TRIGGERED":
                if trigger_source in {"EMA_PENETRATION", "PREVIOUS_DAY_BREAK"}:
                    breakout_line = f"Trigger source: {_html_text(trigger_source)}  Current: {hourly_close}  Trigger: {entry_price}"
                else:
                    breakout_line = f"Previous closed hourly low: {signal_bar_low}  Current: {hourly_close}  Trigger: {entry_price}"
            else:
                breakout_line = f"Current tracked stop: {entry_price}  Current hourly low: {current_low}  Current: {hourly_close}"

        title = f"{dir_emoji} <b>{symbol} · {dir_label}Opportunity</b>"
        if rank is not None and total_ranked is not None:
            rank_prefix = "Triggered" if rank_group == "TRIGGERED" else "Top"
            title = f"🏁 <b>{rank_prefix} {rank}/{total_ranked}</b>\n{title}"
        if signal.get("strong_divergence"):
            title = f"🚨 <b>Strong divergence alert</b>\n{title}"

        return (
            f"{title}\n"
            f"Status: <b>{self._status_label(signal)}</b>\n"
            f"Composite score: {self._score_stars(score)} <code>{score:.1f}/10</code>\n"
            f"{'─' * 32}\n"
            f"<b>Screen 1 · Weekly impulse system</b>\n"
            f"Direction: <b>{_html_text(weekly.get('trend'))}</b>\n"
            f"Impulse color: <b>{_html_text(weekly.get('impulse_color'))}</b>  MACD slope: <code>{self._fmt_num(weekly.get('macd_slope'), 4, signed=True)}</code>\n"
            f"MACD: <code>{self._fmt_num(weekly.get('macd'), 4)}</code>  Signal: <code>{self._fmt_num(weekly.get('macd_signal'), 4)}</code>\n"
            f"Histogram: <code>{self._fmt_num(weekly.get('histogram'), 4, signed=True)}</code>  Change: <code>{self._fmt_num(weekly.get('histogram_delta'), 4, signed=True)}</code>\n"
            f"13EMA: <code>{self._fmt_num(weekly.get('ema13'), 4)}</code>  Slope: <code>{self._fmt_num(weekly.get('ema13_slope'), 4, signed=True)}</code>\n"
            f"Consecutive aligned bars: <code>{weekly.get('confirmed_bars', '—')}</code> / Impulse gate passed: <b>{self._bool_text(weekly.get('impulse_allows_direction'))}</b>\n"
            f"Weekly conclusion: {_html_text(weekly.get('reason'))}\n"
            f"{'─' * 32}\n"
            f"<b>Screen 2 · Daily Force Index</b>\n"
            f"Daily state: <b>{daily_state_label}</b>  Core signals: <code>{daily.get('elder_core_signal_count', 0)}/{daily.get('elder_core_signal_total', 3)}</code>\n"
            f"2-day Force EMA: <code>{self._fmt_num(daily.get('force_index_ema2_prev'), 0, signed=True)}</code> → <code>{self._fmt_num(daily.get('force_index_ema2'), 0, signed=True)}</code>\n"
            f"Daily impulse color: <b>{_html_text(daily.get('impulse_color'))}</b>  Aligned or non-opposing: <b>{self._bool_text(daily.get('same_impulse_or_trend'))}</b>\n"
            f"Aux RSI: <code>{self._fmt_num(daily.get('rsi'), 2)}</code>  HistogramChange: <code>{self._fmt_num(daily.get('momentum_hist_delta'), 4, signed=True)}</code>\n"
            f"13EMA value band: <code>{self._fmt_num(daily.get('value_band_low'), 4)}</code> ~ <code>{self._fmt_num(daily.get('value_band_high'), 4)}</code>  Value-band gap: <code>{self._fmt_num(daily.get('value_band_gap'), 4)}</code>\n"
            f"{daily.get('correction_counter_label', 'Recent correction closes')}: <code>{daily.get('correction_count', 0)}</code>  Structure defense: <code>{self._fmt_num(daily.get('structure_break_level'), 4)}</code>\n"
            f"Force signal: <b>{self._bool_text(daily.get('force_signal'))}</b>  Structure intact: <b>{self._bool_text(daily.get('structure_intact'))}</b>\n"
            f"Aux candle confirmation: <b>{self._bool_text(daily.get('custom_kline_confirmation'))}</b>  Close vs previous close: <b>{self._bool_text(daily.get('custom_close_rule_pass'))}</b>\n"
            f"Wick ratio: <code>{self._fmt_num(daily.get('custom_wick_ratio_pct'), 2)}%</code>  Close location: <code>{self._fmt_num(daily.get('custom_close_location_pct'), 2)}%</code>\n"
            f"Daily conclusion: {_html_text(daily.get('reason'))}\n"
            f"{'─' * 32}\n"
            f"<b>Screen 3 · Entry monitor</b>\n"
            f"EMA penetration reference: <code>{self._fmt_num(entry_plan.get('ema_penetration_entry'), 2)}</code>  Previous-day break reference: <code>{self._fmt_num(entry_plan.get('breakout_entry'), 2)}</code>\n"
            f"Projected next EMA: <code>{self._fmt_num(entry_plan.get('projected_next_ema'), 2)}</code>  Average penetration: <code>{self._fmt_num(entry_plan.get('average_penetration'), 2)}</code>\n"
            f"{breakout_line}\n"
            f"Breakout strength: [{breakout_bar}] {self._fmt_num(hourly.get('breakout_strength'), 2)}xATR\n"
            f"ATR: <code>{self._fmt_num(hourly.get('atr'), 4)}</code>\n"
            f"TriggerStatus: <b>{hourly_status_label}</b>\n"
            f"Hourly conclusion: {_html_text(hourly.get('reason'))}\n"
            f"{'─' * 32}\n"
            f"<b>Entry plan</b>\n"
            f"{entry_options_block}\n"
            f"Initial stops: <code>{self._fmt_num(exits.get('initial_stop_loss'), 2)}</code> ({initial_stop_basis_label})\n"
            f"SafeZone initial stop: <code>{self._fmt_num(exits.get('initial_stop_safezone'), 2)}</code>  Hourly SZ: <code>{self._fmt_num(exits.get('initial_stop_hourly_safezone'), 2)}</code>  Nick stop: <code>{self._fmt_num(exits.get('initial_stop_nick'), 2)}</code>\n"
            f"ATR trailing stop 1x: <code>{self._fmt_num(exits.get('stop_loss_atr_1x'), 2)}</code>  2x: <code>{self._fmt_num(exits.get('stop_loss_atr_2x'), 2)}</code>\n"
            f"Next protective stop: <code>{self._fmt_num(exits.get('protective_stop_loss'), 2)}</code> ({protective_stop_basis_label}, one-way after entry)\n"
            f"Active stop: <code>{self._fmt_num(exits.get('stop_loss'), 2)}</code> ({stop_basis_label})\n"
            f"Stop-method list:\n{self._format_stop_methods(exits.get('stop_methods'))}\n"
            f"First target: <code>{self._fmt_num(exits.get('take_profit'), 2)}</code>  Weekly value zone: <code>{self._fmt_num(weekly_target.get('value_zone_low'), 2)}</code> ~ <code>{self._fmt_num(weekly_target.get('value_zone_high'), 2)}</code>\n"
            f"Daily ATR: <code>{self._fmt_num(exits.get('daily_atr'), 2)}</code>  Thermometer EMA: <code>{self._fmt_num(exits.get('thermometer_ema'), 2)}</code>  Projection base: {self._fmt_num(exits.get('target_reference'), 2)}\n"
            f"Risk per share: {self._fmt_num(exits.get('risk_per_share'), 2)}  Estimated R/R: {self._fmt_num(exits.get('reward_risk_ratio'), 2)}R\n"
            f"Model risk: {self._fmt_num(exits.get('risk_per_share_model'), 2)}  Model R/R: {self._fmt_num(exits.get('reward_risk_ratio_model'), 2)}R\n"
            f"{'─' * 32}\n"
            f"<b>Candidate tags</b>\n"
            f"Candidate date: <code>{signal.get('source_session_date', 'UNKNOWN')}</code>\n"
            f"Earnings status: <b>{_html_text(earnings.get('status', 'UNKNOWN'))}</b>  {_html_text(earnings.get('reason', 'No earnings data'))}\n"
            f"Weekly divergence: {'Yes' if divergence.get('weekly', {}).get('detected') else 'No'}  "
            f"Daily divergence: {'Yes' if divergence.get('daily', {}).get('detected') else 'No'}\n"
            f"{'Strong alert: ' + _html_text(divergence.get('daily', {}).get('exhaustion_reason', '')) if signal.get('strong_divergence') else 'Strong alert: none'}\n"
            f"{'─' * 32}\n"
            f"<i>{_utc_datetime_label()}</i>"
        )

    def send_signal(self, signal: dict) -> bool:
        return self._send(self.format_signal_message(signal))

    def format_position_health_section(self, health_summary: dict[str, Any]) -> str:
        items = list(health_summary.get("items", []))
        total_positions = int(health_summary.get("total_positions", 0) or 0)
        if total_positions == 0:
            return ""

        lines = ["📊 <b>Open-position hourly health check</b>\n"]
        for index, item in enumerate(items[:8], start=1):
            symbol = item.get("symbol", "UNKNOWN")
            direction = self.get_direction_text(item.get("direction"))
            color = str(item.get("hourly_impulse_color", "BLUE")).upper()
            consecutive = int(item.get("consecutive_opposing", 0) or 0)
            divergence = bool(item.get("divergence_detected"))

            color_icon = {"GREEN": "🟢", "RED": "🔴", "BLUE": "🔵"}.get(color, "⚪")
            impulse_note = f"{color_icon} {color}"
            if consecutive >= 2:
                impulse_note += f" x{consecutive}"

            div_note = "  · ⚠️ divergence" if divergence else ""
            lines.append(
                f"{index}. <b>{symbol}</b> {direction} — hourly impulse {impulse_note}{div_note}\n"
            )

        return "".join(lines)

    def format_candidate_summary_message(
        self,
        qualified_signals: list[dict],
        total_candidates: int,
        session_date: str,
        scan_time_sec: float,
        stop_update_summary: dict[str, Any] | None = None,
        open_position_earnings_summary: dict[str, Any] | None = None,
        open_position_exit_alert_summary: dict[str, Any] | None = None,
        position_health_summary: dict[str, Any] | None = None,
    ) -> str:
        if total_candidates <= 0:
            lines = [
                "🔍 <b>No qualified EOD candidates found</b>\n",
                f"Session: <code>{session_date}</code>\n",
            ]
        else:
            strong_divergence_count = sum(1 for s in qualified_signals if s.get("strong_divergence"))
            divergence_note = f" · 🚨 {strong_divergence_count} strong divergence" if strong_divergence_count else ""
            lines = [
                f"📋 <b>EOD watchlist ready — {session_date}</b>\n",
                f"{total_candidates} candidates qualified{divergence_note}\n",
                f"{'─' * 24}\n",
                "✅ Please check the watchlist and place orders before tomorrow's open.\n",
            ]

        if stop_update_summary:
            lines.append(f"\n{self.format_stop_update_section(stop_update_summary)}")
        if open_position_earnings_summary:
            lines.append(f"\n{self.format_open_position_earnings_section(open_position_earnings_summary)}")
        if open_position_exit_alert_summary:
            lines.append(f"\n{self.format_open_position_exit_alert_section(open_position_exit_alert_summary)}")
        if position_health_summary:
            health_section = self.format_position_health_section(position_health_summary)
            if health_section:
                lines.append(f"\n{health_section}")
        lines.append(f"\n<i>Elapsed {scan_time_sec:.1f}s · {_utc_clock_label()}</i>")
        return "".join(lines)

    def format_stop_update_section(self, stop_update_summary: dict[str, Any]) -> str:
        total_positions = int(stop_update_summary.get("total_positions", 0) or 0)
        updated_count = int(stop_update_summary.get("updated_count", 0) or 0)
        unchanged_count = int(stop_update_summary.get("unchanged_count", 0) or 0)
        error_count = int(stop_update_summary.get("error_count", 0) or 0)
        updates = list(stop_update_summary.get("updates", []))

        lines = [
            "🛡 <b>Open-position protective stop update</b>\n",
            f"Positions: {total_positions}  · Updated: {updated_count}  · Unchanged: {unchanged_count}  · Failed: {error_count} \n",
        ]

        display_items = [item for item in updates if item.get("status") in {"UPDATED", "UNCHANGED"}][:8]
        if not display_items and total_positions == 0:
            lines.append("No open trades need stop updates.\n")
            return "".join(lines)
        if not display_items and total_positions > 0:
            lines.append("No ATR trailing-stop changes this run.\n")
            return "".join(lines)

        for index, item in enumerate(display_items, start=1):
            current_stop = item.get("current_stop_loss")
            atr_1x_stop = item.get("proposed_stop_loss")
            atr_2x_stop = item.get("proposed_stop_loss_atr_2x")
            hourly_sz_stop = item.get("proposed_stop_hourly_safezone")
            applied_stop = item.get("applied_stop_loss")
            status = "Updated" if item.get("status") == "UPDATED" else "Unchanged"
            lines.append(
                f"{index}. <b>{item.get('symbol', 'UNKNOWN')}</b> {self.get_direction_text(item.get('direction'))} "
                f"Current stop <code>{self._fmt_num(current_stop, 2)}</code> · "
                f"ATR 1x <code>{self._fmt_num(atr_1x_stop, 2)}</code> · "
                f"ATR 2x <code>{self._fmt_num(atr_2x_stop, 2)}</code> · "
                f"Hourly SZ <code>{self._fmt_num(hourly_sz_stop, 2)}</code> · "
                f"Suggested stop <code>{self._fmt_num(applied_stop, 2)}</code> · {status}\n"
            )
        return "".join(lines)

    def format_open_position_earnings_section(self, earnings_summary: dict[str, Any]) -> str:
        total_positions = int(earnings_summary.get("total_positions", 0) or 0)
        reminder_count = int(earnings_summary.get("reminder_count", 0) or 0)
        window_days = int(earnings_summary.get("window_days", 0) or 0)
        items = list(earnings_summary.get("items", []))

        lines = [
            "📅 <b>Open-position earnings reminders</b>\n",
            f"Positions: {total_positions}  · next {window_days} days: {reminder_count} \n",
        ]
        if total_positions == 0:
            lines.append("No open positions.\n")
            return "".join(lines)
        if not items:
            lines.append(f"No positions enter the earnings window in the next {window_days} days.\n")
            return "".join(lines)

        for index, item in enumerate(items[:8], start=1):
            days_until = int(item.get("days_until", 0) or 0)
            countdown = "today" if days_until == 0 else f"{days_until} days"
            lines.append(
                f"{index}. <b>{item.get('symbol', 'UNKNOWN')}</b> {self.get_direction_text(item.get('direction'))} "
                f"Earnings date <code>{item.get('report_date', 'UNKNOWN')}</code> ({countdown})\n"
                "   Review whether to reduce or exit before earnings gap risk\n"
            )
        return "".join(lines)

    def format_open_position_exit_alert_section(self, exit_summary: dict[str, Any]) -> str:
        total_positions = int(exit_summary.get("total_positions", 0) or 0)
        alert_count = int(exit_summary.get("alert_count", 0) or 0)
        items = list(exit_summary.get("items", []))
        lines = [
            "⚠️ <b>Model-driven loss-risk alert</b>\n",
            f"Positions: {total_positions}  · review required: {alert_count} \n",
        ]
        if total_positions == 0:
            lines.append("No open positions.\n")
            return "".join(lines)
        if not items:
            lines.append("No model-driven reversal risk found in open positions.\n")
            return "".join(lines)
        for index, item in enumerate(items[:8], start=1):
            lines.append(
                f"{index}. <b>{item.get('symbol', 'UNKNOWN')}</b> {self.get_direction_text(item.get('direction'))} "
                f"Weekly {_html_text(item.get('weekly_impulse_color'))} / Daily {_html_text(item.get('daily_impulse_color'))}\n"
                f"   {_html_text(item.get('reason', 'Review whether this position should be closed'))}\n"
            )
        return "".join(lines)

    @staticmethod
    def get_direction_text(value: str | None) -> str:
        return "Short" if str(value or "").strip().lower() == "short" else "Long"

    def format_trigger_summary_message(
        self,
        triggered_signals: list[dict],
        session_date: str,
        total_candidates: int,
        scan_time_sec: float,
    ) -> str:
        if not triggered_signals:
            return (
                f"⏱ <b>Intraday trigger scan complete</b>\n"
                f"Tracking candidate date: <code>{session_date}</code> · active candidates {total_candidates}\n"
                "No actionable entry triggers this run\n"
                f"<i>Elapsed {scan_time_sec:.1f}s · {_utc_clock_label()}</i>"
            )

        lines = [
            f"🏁 <b>Triggered Opportunity ({len(triggered_signals)})</b>\n",
            f"Tracking candidate date: <code>{session_date}</code> · active candidates {total_candidates}\n",
            f"* = Triggered\n",
            f"{'─' * 24}\n",
        ]
        for index, signal in enumerate(triggered_signals, start=1):
            direction = "Long" if signal["direction"] == "LONG" else "Short"
            divergence_badge = " 🚨 divergence" if signal.get("strong_divergence") else ""
            exits = signal["exits"]
            hourly = signal.get("hourly", {})
            status_label = self._status_label(signal)
            entry_options = hourly.get("entry_options") or []
            ema_option = self._entry_option_by_code(entry_options, "EMA_PENETRATION")
            breakout_option = self._entry_option_by_code(entry_options, "PREVIOUS_DAY_BREAK")
            lines.append(
                f"{index}. <b>{signal['symbol']}</b> {direction} "
                f"{status_label} Current {self._fmt_num(hourly.get('close'), 2)}{divergence_badge}\n"
                f"   Suggested entry: <code>{self._fmt_num(exits.get('entry'), 2)}</code>  "
                f"Reason: {_html_text(self._format_trigger_reason(signal))}\n"
                f"   Entry: EMA <code>{self._format_entry_option_price(ema_option)}</code>  "
                f"Breakout <code>{self._format_entry_option_price(breakout_option)}</code>\n"
                f"   Stop: SafeZone <code>{self._fmt_num(exits.get('initial_stop_safezone'), 2)}</code>  "
                f"Hourly SZ <code>{self._fmt_num(exits.get('initial_stop_hourly_safezone'), 2)}</code>  "
                f"Nick <code>{self._fmt_num(exits.get('initial_stop_nick'), 2)}</code>\n"
            )

        lines.append(f"\n<i>Elapsed {scan_time_sec:.1f}s · {_utc_clock_label()}</i>")
        return "".join(lines)

    def format_premarket_review_summary(
        self,
        review_items: list[dict],
        session_date: str,
        scan_time_sec: float,
    ) -> str:
        if not review_items:
            return (
                f"🌅 <b>Premarket order review complete</b>\n"
                f"Candidate date: <code>{session_date}</code>\n"
                "No candidate order plans to review.\n"
                f"<i>Elapsed {scan_time_sec:.1f}s · {_utc_clock_label()}</i>"
            )

        ready_count = sum(1 for item in review_items if item.get("status") == "READY")
        review_count = sum(1 for item in review_items if item.get("status") == "REVIEW")
        blocked_count = sum(1 for item in review_items if item.get("status") == "BLOCKED")
        lines = [
            f"🌅 <b>Premarket order review</b>\n",
            f"Candidate date: <code>{session_date}</code> · Ready {ready_count} / Review {review_count} / Blocked {blocked_count}\n",
            f"{'─' * 24}\n",
        ]
        for index, item in enumerate(review_items[:12], start=1):
            order = (item.get("order_plan") or {}).get("primary_order") or {}
            checks = item.get("checks") or []
            warnings = [check for check in checks if check.get("severity") in {"WARN", "BLOCK"}]
            status = item.get("status", "REVIEW")
            status_label = "Ready" if status == "READY" else "Blocked" if status == "BLOCKED" else "Review"
            lines.append(
                f"{index}. <b>{item.get('symbol', 'UNKNOWN')}</b> {self.get_direction_text(item.get('direction'))} · {status_label}\n"
                f"   Stop <code>{self._fmt_num(order.get('stop_price'), 2)}</code> "
                f"Limit <code>{self._fmt_num(order.get('limit_price'), 2)}</code> "
                f"Reference <code>{self._fmt_num(item.get('current_reference_price'), 2)}</code> "
                f"Gap <code>{self._fmt_num(item.get('gap_pct'), 2)}%</code>\n"
            )
            for warning in warnings[:2]:
                lines.append(f"   {_html_text(warning.get('message', 'Manual review required'))}\n")
        lines.append(f"\n<i>Elapsed {scan_time_sec:.1f}s · {_utc_clock_label()}</i>")
        return "".join(lines)

    def send_premarket_review_summary(
        self,
        review_items: list[dict],
        session_date: str,
        scan_time_sec: float,
    ) -> bool:
        return self._send(self.format_premarket_review_summary(review_items, session_date, scan_time_sec))

    def send_candidate_summary(
        self,
        qualified_signals: list[dict],
        total_candidates: int,
        session_date: str,
        scan_time_sec: float,
        stop_update_summary: dict[str, Any] | None = None,
        open_position_earnings_summary: dict[str, Any] | None = None,
        open_position_exit_alert_summary: dict[str, Any] | None = None,
        position_health_summary: dict[str, Any] | None = None,
    ) -> bool:
        return self._send(
            self.format_candidate_summary_message(
                qualified_signals,
                total_candidates,
                session_date,
                scan_time_sec,
                stop_update_summary=stop_update_summary,
                open_position_earnings_summary=open_position_earnings_summary,
                open_position_exit_alert_summary=open_position_exit_alert_summary,
                position_health_summary=position_health_summary,
            )
        )

    def send_trigger_summary(
        self,
        triggered_signals: list[dict],
        session_date: str,
        total_candidates: int,
        scan_time_sec: float,
    ) -> bool:
        return self._send(
            self.format_trigger_summary_message(
                triggered_signals,
                session_date,
                total_candidates,
                scan_time_sec,
            )
        )

    def send_divergence_alert(self, symbol: str, direction: str, divergence_reason: str) -> bool:
        direction_label = self.get_direction_text(direction)
        div_type = "bearish" if direction == "LONG" else "bullish"
        return self._send(
            f"⚠️ <b>Hourly divergence — {symbol} {direction_label}</b>\n"
            f"{div_type} divergence forming on hourly chart: consider tightening stop or reviewing position size\n"
            f"<i>{_html_text(divergence_reason)}</i>\n"
            f"<i>{_utc_clock_label()}</i>"
        )

    def send_no_opportunity(self, scan_time_sec: float) -> bool:
        return self._send(
            "🔍 <b>No qualified trading opportunities found</b>\n"
            f"<i>Elapsed {scan_time_sec:.1f}s · {_utc_clock_label()}</i>"
        )

    def send_error(self, error_message: str) -> bool:
        return self._send(
            f"❗ <b>System error</b>\n<code>{error_message}</code>\n"
            f"<i>{_utc_clock_label()}</i>"
        )
