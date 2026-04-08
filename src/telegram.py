from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

from schema import TelegramConfig

logger = logging.getLogger(__name__)


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
            "TWO_BAR": "日线两根K结构止损",
            "UNKNOWN": "保护止损",
        }
        return labels.get(stop_basis, stop_basis)

    @staticmethod
    def _candidate_score(signal: dict) -> float:
        return float(signal.get("candidate_score", signal.get("signal_score", 0.0)) or 0.0)

    @staticmethod
    def _execution_score(signal: dict) -> float:
        return float(signal.get("execution_score", signal.get("signal_score", 0.0)) or 0.0)

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
        stop_basis_label = self._stop_basis_label(exits["stop_basis"])
        hist_bar = self._bar(abs(weekly["histogram"]) * 1000, 5, length=8)
        rsi_bar = self._bar(daily["rsi"], 100, length=10)
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
            f"MACD：{weekly['macd']:.4f}  Signal：{weekly['macd_signal']:.4f}\n"
            f"Histogram：<code>{weekly['histogram']:+.4f}</code>\n"
            f"强度条：[{hist_bar}]  连续确认：{weekly['confirmed_bars']} 根\n"
            f"解读：{weekly['reason']}\n"
            f"{'─' * 32}\n"
            f"<b>第二重 · 日线 RSI</b>\n"
            f"RSI(14)：<code>{daily['rsi']:.1f}</code> ({daily_state_label})\n"
            f"上根 RSI：{daily['rsi_prev']:.1f}\n"
            f"RSI 条：[{rsi_bar}] {daily['rsi']:.0f}\n"
            f"解读：{daily['reason']}\n"
            f"{'─' * 32}\n"
            f"<b>第三重 · 1小时突破</b>\n"
            f"{breakout_line}\n"
            f"突破强度：[{breakout_bar}] {hourly.get('breakout_strength', 0):.2f}xATR\n"
            f"ATR：<code>{hourly['atr']:.4f}</code>\n"
            f"触发状态：<b>{hourly_status_label}</b>\n"
            f"解读：{hourly['reason']}\n"
            f"{'─' * 32}\n"
            f"<b>交易建议</b>\n"
            f"建议入场：<code>{exits['entry']:.2f}</code>\n"
            f"保护止损：<code>{exits['stop_loss']:.2f}</code> ({stop_basis_label})\n"
            f"日线 SafeZone：<code>{exits['stop_loss_safezone']:.2f}</code>  日线两根K：<code>{exits['stop_loss_two_bar']:.2f}</code>\n"
            f"首个止盈：<code>{exits['take_profit']:.2f}</code>\n"
            f"日线 Thermometer EMA：<code>{exits['thermometer_ema']:.2f}</code>  投影基准：{exits['target_reference']:.2f}\n"
            f"每股风险：{exits['risk_per_share']:.2f}  预估盈亏比：{exits['reward_risk_ratio']:.2f}R\n"
            f"{'─' * 32}\n"
            f"<b>候选池标签</b>\n"
            f"候选日期：<code>{signal.get('source_session_date', 'UNKNOWN')}</code>\n"
            f"财报状态：<b>{earnings.get('status', 'UNKNOWN')}</b>  {earnings.get('reason', '未获取到财报信息')}\n"
            f"周线背离：{'是' if divergence.get('weekly', {}).get('detected') else '否'}  "
            f"日线背离：{'是' if divergence.get('daily', {}).get('detected') else '否'}\n"
            f"{'强提醒：' + divergence.get('daily', {}).get('exhaustion_reason', '') if signal.get('strong_divergence') else '强提醒：无'}\n"
            f"{'─' * 32}\n"
            f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>"
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
    ) -> str:
        if total_candidates <= 0:
            message = (
                "🔍 <b>本轮收盘后未发现符合条件的交易候选</b>\n"
                f"<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>"
            )
            if stop_update_summary:
                message = f"{message}\n\n{self.format_stop_update_section(stop_update_summary)}"
            return message
        if not qualified_signals:
            message = (
                f"📘 <b>{session_date} 候选池更新完成</b>\n"
                f"共筛出 {total_candidates} 个合格标的，但当前展示条数配置为 0\n"
                f"<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>"
            )
            if stop_update_summary:
                message = f"{message}\n\n{self.format_stop_update_section(stop_update_summary)}"
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
                f"   {daily_state} · 后续盘中持续跟踪小时线 stop · 财报 {earnings_status}\n"
            )

        if stop_update_summary:
            lines.append(f"\n{self.format_stop_update_section(stop_update_summary)}")
        lines.append(f"\n<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>")
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
                f"<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>"
            )

        lines = [
            f"🏁 <b>Top {len(triggered_signals)} Triggered 机会</b>\n",
            f"跟踪候选日期：<code>{session_date}</code> · 活跃候选总数 {total_candidates}\n",
            f"{'─' * 24}\n",
        ]
        for index, signal in enumerate(triggered_signals, start=1):
            direction = "做多" if signal["direction"] == "LONG" else "做空"
            divergence_badge = " 🚨背离" if signal.get("strong_divergence") else ""
            lines.append(
                f"{index}. <b>{signal['symbol']}</b> {direction} "
                f"执行分 {self._execution_score(signal):.1f}{divergence_badge}\n"
                f"   现价 {signal['hourly']['close']:.2f} · RR {signal['exits']['reward_risk_ratio']:.2f}R · "
                f"财报 {signal.get('earnings', {}).get('status', 'UNKNOWN')}\n"
            )

        lines.append(f"\n<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>")
        return "".join(lines)

    def send_candidate_summary(
        self,
        qualified_signals: list[dict],
        total_candidates: int,
        session_date: str,
        scan_time_sec: float,
        stop_update_summary: dict[str, Any] | None = None,
    ) -> bool:
        return self._send(
            self.format_candidate_summary_message(
                qualified_signals,
                total_candidates,
                session_date,
                scan_time_sec,
                stop_update_summary=stop_update_summary,
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
            f"<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>"
        )

    def send_error(self, error_message: str) -> bool:
        return self._send(
            f"❗ <b>系统错误</b>\n<code>{error_message}</code>\n"
            f"<i>{datetime.utcnow().strftime('%H:%M UTC')}</i>"
        )
