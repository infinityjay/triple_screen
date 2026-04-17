from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import requests

from config.schema import TelegramConfig

logger = logging.getLogger(__name__)


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
        pct = min(value / max_value, 1.0) if max_value > 0 else 0
        filled = int(pct * length)
        return fill * filled + empty * (length - filled)

    @staticmethod
    def _score_stars(score: float) -> str:
        stars = int(score / 2)
        return "⭐" * stars + "☆" * (5 - stars)

    @staticmethod
    def _bool_text(value: Any) -> str:
        return "是" if bool(value) else "否"

    @staticmethod
    def _status_label(signal: dict) -> str:
        if signal.get("opportunity_status") == "TRIGGERED":
            return "已触发"
        return "观察中"

    @staticmethod
    def _daily_state_label(state: str) -> str:
        labels = {
            "RECOVERING": "超卖后回升",
            "ROLLING_OVER": "超买后回落",
            "OVERSOLD": "超卖",
            "OVERBOUGHT": "超买",
            "OVERSOLD_WAIT": "超卖中等待拐头",
            "OVERBOUGHT_WAIT": "超买中等待拐头",
            "POST_OVERSOLD_WATCH": "超卖后观察",
            "POST_OVERBOUGHT_WATCH": "超买后观察",
            "PULLBACK_WATCH": "回调观察",
            "RALLY_WATCH": "反弹观察",
            "NO_PULLBACK": "未形成清晰回调",
            "NO_RALLY": "未形成清晰反弹",
            "STRUCTURE_BROKEN": "结构被破坏",
            "ACCELERATING_PULLBACK": "回调仍在加速",
            "ACCELERATING_RALLY": "反弹仍在加速",
            "PULLBACK_WAIT_VALUE_BAND": "等待回到 13EMA 价值带",
            "RALLY_WAIT_VALUE_BAND": "等待回到 13EMA 价值带",
            "PULLBACK_WAIT_HISTOGRAM": "已回到价值带，等待 Histogram 回升",
            "RALLY_WAIT_HISTOGRAM": "已回到价值带，等待 Histogram 回落",
            "PULLBACK_HISTOGRAM_TURNED": "价值带内 Histogram 已回升",
            "RALLY_HISTOGRAM_TURNED": "价值带内 Histogram 已回落",
            "NEUTRAL": "中性",
        }
        return labels.get(state, state)

    @staticmethod
    def _hourly_status_label(status: str) -> str:
        labels = {
            "TRIGGERED": "已触发",
            "WAITING_BREAKOUT": "等待向上突破",
            "WAITING_BREAKDOWN": "等待向下跌破",
            "WAITING_NEXT_BAR": "等待下一根小时K开始跟踪",
            "NEUTRAL": "中性",
        }
        return labels.get(status, status)

    @staticmethod
    def _stop_basis_label(stop_basis: str) -> str:
        labels = {
            "SAFEZONE": "日线 SafeZone 止损",
            "SIGNAL_BAR": "小时信号K止损",
            "PULLBACK_PIVOT": "日线回调摆点止损",
            "MANUAL": "手动录入止损",
            "UNKNOWN": "保护止损",
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
            return "暂无多止损方法明细"

        initial_methods = [method for method in methods if method.get("group") == "initial"]
        trailing_methods = [method for method in methods if method.get("group") == "trailing"]

        def build_lines(title: str, items: list[dict[str, Any]]) -> list[str]:
            if not items:
                return [f"{title}：暂无"]
            lines = [title]
            for method in items[:4]:
                price = "需手工判断" if method.get("price") is None else self._fmt_num(method.get("price"), 2)
                lines.append(f"• {method.get('label', '止损方法')}：<code>{price}</code> {method.get('suitable_for', '')}")
            return lines

        return "\n".join(build_lines("初始止损", initial_methods) + build_lines("跟踪止损", trailing_methods))

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

        dir_emoji = "🚀" if direction == "LONG" else "🔻"
        dir_label = "做多" if direction == "LONG" else "做空"
        daily_state_label = self._daily_state_label(daily["rsi_state"])
        hourly_status_label = self._hourly_status_label(hourly["status"])
        stop_basis_label = self._stop_basis_label(exits.get("stop_basis", "UNKNOWN"))
        initial_stop_basis_label = self._stop_basis_label(exits.get("initial_stop_basis", exits.get("stop_basis", "UNKNOWN")))
        protective_stop_basis_label = self._stop_basis_label(exits.get("protective_stop_basis", "SAFEZONE"))
        breakout_bar = self._bar(hourly.get("breakout_strength", 0), 1.0, length=8)

        if direction == "LONG":
            if signal.get("opportunity_status") == "TRIGGERED":
                breakout_line = (
                    f"上一根已收盘高点：{hourly['signal_bar_high']:.2f}  当前价：{hourly['close']:.2f}  触发价：{hourly['entry_price']:.2f}"
                )
            else:
                breakout_line = (
                    f"当前跟踪 stop：{hourly['entry_price']:.2f}  当前小时高点：{hourly['current_high']:.2f}  当前价：{hourly['close']:.2f}"
                )
        else:
            if signal.get("opportunity_status") == "TRIGGERED":
                breakout_line = (
                    f"上一根已收盘低点：{hourly['signal_bar_low']:.2f}  当前价：{hourly['close']:.2f}  触发价：{hourly['entry_price']:.2f}"
                )
            else:
                breakout_line = (
                    f"当前跟踪 stop：{hourly['entry_price']:.2f}  当前小时低点：{hourly['current_low']:.2f}  当前价：{hourly['close']:.2f}"
                )

        title = f"{dir_emoji} <b>{symbol} · {dir_label}机会</b>"
        if rank is not None and total_ranked is not None:
            rank_prefix = "Triggered Top" if rank_group == "TRIGGERED" else "Top"
            title = f"🏁 <b>{rank_prefix} {rank}/{total_ranked}</b>\n{title}"
        if signal.get("strong_divergence"):
            title = f"🚨 <b>强背离提醒</b>\n{title}"

        return (
            f"{title}\n"
            f"状态：<b>{self._status_label(signal)}</b>\n"
            f"综合评分：{self._score_stars(score)} <code>{score:.1f}/10</code>\n"
            f"{'─' * 32}\n"
            f"<b>第一重 · 周线 MACD</b>\n"
            f"方向：<b>{weekly['trend']}</b>\n"
            f"MACD：<code>{self._fmt_num(weekly.get('macd'), 4)}</code>  Signal：<code>{self._fmt_num(weekly.get('macd_signal'), 4)}</code>\n"
            f"Histogram：<code>{self._fmt_num(weekly.get('histogram'), 4, signed=True)}</code>  变化：<code>{self._fmt_num(weekly.get('histogram_delta'), 4, signed=True)}</code>\n"
            f"13EMA：<code>{self._fmt_num(weekly.get('ema13'), 4)}</code>  斜率：<code>{self._fmt_num(weekly.get('ema13_slope'), 4, signed=True)}</code>\n"
            f"连续同向 bars：<code>{weekly.get('confirmed_bars', '—')}</code> / 收盘位于趋势侧：<b>{self._bool_text(weekly.get('close_on_trend_side'))}</b>\n"
            f"周线结论：{weekly['reason']}\n"
            f"{'─' * 32}\n"
            f"<b>第二重 · 日线 Elder 核心</b>\n"
            f"日线阶段：<b>{daily_state_label}</b>  Elder核心信号：<code>{daily.get('elder_core_signal_count', 0)}/{daily.get('elder_core_signal_total', 3)}</code>\n"
            f"Histogram：<code>{self._fmt_num(daily.get('momentum_hist_now'), 4, signed=True)}</code>  前一日：<code>{self._fmt_num(daily.get('momentum_hist_prev'), 4, signed=True)}</code>  变化：<code>{self._fmt_num(daily.get('momentum_hist_delta'), 4, signed=True)}</code>\n"
            f"13EMA价值带：<code>{self._fmt_num(daily.get('value_band_low'), 4)}</code> ~ <code>{self._fmt_num(daily.get('value_band_high'), 4)}</code>  距离价值带：<code>{self._fmt_num(daily.get('value_band_gap'), 4)}</code>\n"
            f"{daily.get('correction_counter_label', '近8日修正收盘数')}：<code>{daily.get('correction_count', 0)}</code>  结构防守位：<code>{self._fmt_num(daily.get('structure_break_level'), 4)}</code>\n"
            f"回到价值带：<b>{self._bool_text(daily.get('value_zone_reached'))}</b>  Histogram转向：<b>{self._bool_text(daily.get('histogram_reversal', daily.get('momentum_reversal')))}</b>\n"
            f"辅助K线确认：<b>{self._bool_text(daily.get('custom_kline_confirmation'))}</b>  收盘相对昨收：<b>{self._bool_text(daily.get('custom_close_rule_pass'))}</b>\n"
            f"影线比例：<code>{self._fmt_num(daily.get('custom_wick_ratio_pct'), 2)}%</code>  收盘位置：<code>{self._fmt_num(daily.get('custom_close_location_pct'), 2)}%</code>\n"
            f"日线结论：{daily['reason']}\n"
            f"{'─' * 32}\n"
            f"<b>第三重 · 1小时突破</b>\n"
            f"{breakout_line}\n"
            f"突破强度：[{breakout_bar}] {hourly.get('breakout_strength', 0):.2f}xATR\n"
            f"ATR：<code>{hourly['atr']:.4f}</code>\n"
            f"触发状态：<b>{hourly_status_label}</b>\n"
            f"小时线结论：{hourly['reason']}\n"
            f"{'─' * 32}\n"
            f"<b>交易建议</b>\n"
            f"建议入场：<code>{self._fmt_num(exits.get('entry'), 2)}</code>\n"
            f"初始止损：<code>{self._fmt_num(exits.get('initial_stop_loss'), 2)}</code> ({initial_stop_basis_label})\n"
            f"信号K止损：<code>{self._fmt_num(exits.get('initial_stop_signal_bar'), 2)}</code>  两根K极值：<code>{self._fmt_num(exits.get('initial_stop_two_bar'), 2)}</code>\n"
            f"修正摆点：<code>{self._fmt_num(exits.get('initial_stop_pullback_pivot'), 2)}</code>  SafeZone：<code>{self._fmt_num(exits.get('stop_loss_safezone'), 2)}</code>\n"
            f"Chandelier：<code>{self._fmt_num(exits.get('stop_loss_chandelier'), 2)}</code>  Parabolic：<code>{self._fmt_num(exits.get('stop_loss_parabolic'), 2)}</code>\n"
            f"后续保护止损：<code>{self._fmt_num(exits.get('protective_stop_loss'), 2)}</code> ({protective_stop_basis_label}，持仓后单向推进)\n"
            f"当前激活止损：<code>{self._fmt_num(exits.get('stop_loss'), 2)}</code> ({stop_basis_label})\n"
            f"可选止损清单：\n{self._format_stop_methods(exits.get('stop_methods'))}\n"
            f"首个止盈：<code>{self._fmt_num(exits.get('take_profit'), 2)}</code>\n"
            f"日线 Thermometer EMA：<code>{self._fmt_num(exits.get('thermometer_ema'), 2)}</code>  投影基准：{self._fmt_num(exits.get('target_reference'), 2)}\n"
            f"每股风险：{self._fmt_num(exits.get('risk_per_share'), 2)}  预估盈亏比：{self._fmt_num(exits.get('reward_risk_ratio'), 2)}R\n"
            f"{'─' * 32}\n"
            f"<b>候选池标签</b>\n"
            f"候选日期：<code>{signal.get('source_session_date', 'UNKNOWN')}</code>\n"
            f"财报状态：<b>{earnings.get('status', 'UNKNOWN')}</b>  {earnings.get('reason', '未获取到财报信息')}\n"
            f"周线背离：{'是' if divergence.get('weekly', {}).get('detected') else '否'}  "
            f"日线背离：{'是' if divergence.get('daily', {}).get('detected') else '否'}\n"
            f"{'强提醒：' + divergence.get('daily', {}).get('exhaustion_reason', '') if signal.get('strong_divergence') else '强提醒：无'}\n"
            f"{'─' * 32}\n"
            f"<i>{_utc_datetime_label()}</i>"
        )

    def send_signal(self, signal: dict) -> bool:
        return self._send(self.format_signal_message(signal))

    def format_candidate_summary_message(
        self,
        qualified_signals: list[dict],
        total_candidates: int,
        session_date: str,
        scan_time_sec: float,
        stop_update_summary: dict[str, Any] | None = None,
        open_position_earnings_summary: dict[str, Any] | None = None,
    ) -> str:
        if total_candidates <= 0:
            message = (
                "🔍 <b>本轮收盘后未发现符合条件的交易候选</b>\n"
                f"<i>耗时 {scan_time_sec:.1f}s · {_utc_clock_label()}</i>"
            )
            if stop_update_summary:
                message = f"{message}\n\n{self.format_stop_update_section(stop_update_summary)}"
            if open_position_earnings_summary:
                message = f"{message}\n\n{self.format_open_position_earnings_section(open_position_earnings_summary)}"
            return message
        if not qualified_signals:
            message = (
                f"📘 <b>{session_date} 候选池更新完成</b>\n"
                f"共筛出 {total_candidates} 个合格标的，但当前展示条数配置为 0\n"
                f"<i>耗时 {scan_time_sec:.1f}s · {_utc_clock_label()}</i>"
            )
            if stop_update_summary:
                message = f"{message}\n\n{self.format_stop_update_section(stop_update_summary)}"
            if open_position_earnings_summary:
                message = f"{message}\n\n{self.format_open_position_earnings_section(open_position_earnings_summary)}"
            return message

        strong_divergence_count = sum(1 for signal in qualified_signals if signal.get("strong_divergence"))
        lines = [
            f"📘 <b>{session_date} 候选池更新完成</b>\n",
            f"共筛出 {total_candidates} 个合格标的，本消息展示前 {len(qualified_signals)} 个\n",
            f"强背离提醒 {strong_divergence_count} 个\n",
            f"{'─' * 24}\n",
        ]

        for index, signal in enumerate(qualified_signals, start=1):
            direction = "做多" if signal["direction"] == "LONG" else "做空"
            daily_state = self._daily_state_label(signal["daily"]["rsi_state"])
            divergence_badge = " 🚨背离" if signal.get("strong_divergence") else ""
            earnings_status = signal.get("earnings", {}).get("status", "UNKNOWN")
            lines.append(
                f"{index}. <b>{signal['symbol']}</b> {direction} 候选 "
                f"候选分 {self._candidate_score(signal):.1f}{divergence_badge}\n"
                f"   {daily_state} · Elder核心 {signal['daily'].get('elder_core_signal_count', 0)}/{signal['daily'].get('elder_core_signal_total', 3)} · "
                f"价值带 {self._bool_text(signal['daily'].get('value_zone_reached'))} · 财报 {earnings_status}\n"
            )

        if stop_update_summary:
            lines.append(f"\n{self.format_stop_update_section(stop_update_summary)}")
        if open_position_earnings_summary:
            lines.append(f"\n{self.format_open_position_earnings_section(open_position_earnings_summary)}")
        lines.append(f"\n<i>耗时 {scan_time_sec:.1f}s · {_utc_clock_label()}</i>")
        return "".join(lines)

    def format_stop_update_section(self, stop_update_summary: dict[str, Any]) -> str:
        total_positions = int(stop_update_summary.get("total_positions", 0) or 0)
        updated_count = int(stop_update_summary.get("updated_count", 0) or 0)
        unchanged_count = int(stop_update_summary.get("unchanged_count", 0) or 0)
        error_count = int(stop_update_summary.get("error_count", 0) or 0)
        updates = list(stop_update_summary.get("updates", []))

        lines = [
            "🛡 <b>持仓保护止损更新</b>\n",
            f"持仓 {total_positions} 笔 · 更新 {updated_count} 笔 · 未变 {unchanged_count} 笔 · 失败 {error_count} 笔\n",
        ]

        display_items = [item for item in updates if item.get("status") == "UPDATED"][:8]
        if not display_items and total_positions == 0:
            lines.append("当前没有未平仓交易需要更新。\n")
            return "".join(lines)
        if not display_items and total_positions > 0:
            lines.append("本轮没有需要上调/下移的保护性止损。\n")
            return "".join(lines)

        for index, item in enumerate(display_items, start=1):
            previous_stop = item.get("previous_stop_loss")
            applied_stop = item.get("applied_stop_loss")
            stop_basis = item.get("stop_basis", "UNKNOWN")
            lines.append(
                f"{index}. <b>{item.get('symbol', 'UNKNOWN')}</b> {self.get_direction_text(item.get('direction'))} "
                f"{previous_stop if previous_stop is not None else '—'} → {applied_stop if applied_stop is not None else '—'} "
                f"({stop_basis})\n"
            )
        return "".join(lines)

    def format_open_position_earnings_section(self, earnings_summary: dict[str, Any]) -> str:
        total_positions = int(earnings_summary.get("total_positions", 0) or 0)
        reminder_count = int(earnings_summary.get("reminder_count", 0) or 0)
        window_days = int(earnings_summary.get("window_days", 0) or 0)
        items = list(earnings_summary.get("items", []))

        lines = [
            "📅 <b>持仓临近财报提醒</b>\n",
            f"持仓 {total_positions} 笔 · 未来 {window_days} 天内需留意 {reminder_count} 笔\n",
        ]
        if total_positions == 0:
            lines.append("当前没有未平仓交易。\n")
            return "".join(lines)
        if not items:
            lines.append(f"当前没有持仓在未来 {window_days} 天内进入财报窗口。\n")
            return "".join(lines)

        for index, item in enumerate(items[:8], start=1):
            days_until = int(item.get("days_until", 0) or 0)
            countdown = "今天" if days_until == 0 else f"{days_until} 天后"
            lines.append(
                f"{index}. <b>{item.get('symbol', 'UNKNOWN')}</b> {self.get_direction_text(item.get('direction'))} "
                f"财报日 <code>{item.get('report_date', 'UNKNOWN')}</code> ({countdown})\n"
                "   建议检查是否需要提前卖出或减仓，避免财报跳空风险\n"
            )
        return "".join(lines)

    @staticmethod
    def get_direction_text(value: str | None) -> str:
        return "做空" if str(value or "").strip().lower() == "short" else "做多"

    def format_trigger_summary_message(
        self,
        triggered_signals: list[dict],
        session_date: str,
        total_candidates: int,
        scan_time_sec: float,
    ) -> str:
        if not triggered_signals:
            return (
                f"⏱ <b>盘中触发扫描完成</b>\n"
                f"跟踪候选日期：<code>{session_date}</code> · 活跃候选总数 {total_candidates}\n"
                "本轮暂无满足条件的触发机会\n"
                f"<i>耗时 {scan_time_sec:.1f}s · {_utc_clock_label()}</i>"
            )

        lines = [
            f"🏁 <b>Top {len(triggered_signals)} Triggered 机会</b>\n",
            f"跟踪候选日期：<code>{session_date}</code> · 活跃候选总数 {total_candidates}\n",
            f"{'─' * 24}\n",
        ]
        for index, signal in enumerate(triggered_signals, start=1):
            direction = "做多" if signal["direction"] == "LONG" else "做空"
            entry_label = "买入价" if signal["direction"] == "LONG" else "卖出价"
            divergence_badge = " 🚨背离" if signal.get("strong_divergence") else ""
            lines.append(
                f"{index}. <b>{signal['symbol']}</b> {direction} "
                f"执行分 {self._execution_score(signal):.1f}{divergence_badge}\n"
                f"   现价 {signal['hourly']['close']:.2f} · {entry_label} {signal['exits']['entry']:.2f} · "
                f"初始止损 {signal['exits']['initial_stop_loss']:.2f}\n"
                f"   RR {signal['exits']['reward_risk_ratio']:.2f}R · "
                f"Elder核心 {signal['daily'].get('elder_core_signal_count', 0)}/{signal['daily'].get('elder_core_signal_total', 3)} · "
                f"财报 {signal.get('earnings', {}).get('status', 'UNKNOWN')}\n"
            )

        lines.append(f"\n<i>耗时 {scan_time_sec:.1f}s · {_utc_clock_label()}</i>")
        return "".join(lines)

    def send_candidate_summary(
        self,
        qualified_signals: list[dict],
        total_candidates: int,
        session_date: str,
        scan_time_sec: float,
        stop_update_summary: dict[str, Any] | None = None,
        open_position_earnings_summary: dict[str, Any] | None = None,
    ) -> bool:
        return self._send(
            self.format_candidate_summary_message(
                qualified_signals,
                total_candidates,
                session_date,
                scan_time_sec,
                stop_update_summary=stop_update_summary,
                open_position_earnings_summary=open_position_earnings_summary,
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

    def send_no_opportunity(self, scan_time_sec: float) -> bool:
        return self._send(
            "🔍 <b>本轮扫描未发现符合条件的交易机会</b>\n"
            f"<i>耗时 {scan_time_sec:.1f}s · {_utc_clock_label()}</i>"
        )

    def send_error(self, error_message: str) -> bool:
        return self._send(
            f"❗ <b>系统错误</b>\n<code>{error_message}</code>\n"
            f"<i>{_utc_clock_label()}</i>"
        )
