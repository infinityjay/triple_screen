from __future__ import annotations

import logging
from datetime import datetime

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

    def format_signal_message(self, signal: dict) -> str:
        direction = signal["direction"]
        symbol = signal["symbol"]
        score = signal.get("signal_score", 0)
        rank = signal.get("rank")
        total_ranked = signal.get("total_ranked")
        weekly = signal["weekly"]
        daily = signal["daily"]
        hourly = signal["hourly"]
        exits = signal["exits"]

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
                    f"上一根高点：{hourly['signal_bar_high']:.2f}  当前价：{hourly['close']:.2f}  触发价：{hourly['entry_price']:.2f}"
                )
            else:
                breakout_line = (
                    f"当前小时高点：{hourly['current_high']:.2f}  当前价：{hourly['close']:.2f}  下一触发价：{hourly['entry_price']:.2f}"
                )
        else:
            if signal.get("opportunity_status") == "TRIGGERED":
                breakout_line = (
                    f"上一根低点：{hourly['signal_bar_low']:.2f}  当前价：{hourly['close']:.2f}  触发价：{hourly['entry_price']:.2f}"
                )
            else:
                breakout_line = (
                    f"当前小时低点：{hourly['current_low']:.2f}  当前价：{hourly['close']:.2f}  下一触发价：{hourly['entry_price']:.2f}"
                )

        title = f"{dir_emoji} <b>{symbol} · {dir_label}机会</b>"
        if rank is not None and total_ranked is not None:
            title = f"🏁 <b>Top {rank}/{total_ranked}</b>\n{title}"

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
            f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>"
        )

    def send_signal(self, signal: dict) -> bool:
        return self._send(self.format_signal_message(signal))

    def format_summary_message(self, signals: list[dict], scan_time_sec: float) -> str:
        if not signals:
            return (
                "🔍 <b>本轮扫描未发现符合条件的前三交易机会</b>\n"
                f"<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>"
            )

        triggered_count = sum(1 for signal in signals if signal.get("opportunity_status") == "TRIGGERED")
        lines = [
            f"📋 <b>Top {len(signals)} 交易机会摘要</b>\n",
            f"已触发 {triggered_count} 个 · 待触发 {len(signals) - triggered_count} 个\n",
            f"{'─' * 24}\n",
        ]

        for index, signal in enumerate(signals, start=1):
            direction = "做多" if signal["direction"] == "LONG" else "做空"
            status = "已触发" if signal.get("opportunity_status") == "TRIGGERED" else "待触发"
            daily_state = self._daily_state_label(signal["daily"]["rsi_state"])
            hourly = signal["hourly"]
            trigger_text = (
                f"现价 {hourly['close']:.2f}"
                if signal.get("opportunity_status") == "TRIGGERED"
                else f"触发价 {hourly['entry_price']:.2f}"
            )
            lines.append(
                f"{index}. <b>{signal['symbol']}</b> {direction} {status} "
                f"评分 {signal['signal_score']:.1f}\n"
                f"   {daily_state} · {trigger_text} · "
                f"SL {signal['exits']['stop_loss']:.2f} · TP {signal['exits']['take_profit']:.2f}\n"
            )

        lines.append(f"\n<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>")
        return "".join(lines)

    def send_summary(self, signals: list[dict], scan_time_sec: float) -> bool:
        return self._send(self.format_summary_message(signals, scan_time_sec))

    def send_no_opportunity(self, scan_time_sec: float) -> bool:
        return self._send(
            "🔍 <b>本轮扫描未发现符合条件的前三交易机会</b>\n"
            f"<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>"
        )

    def send_error(self, error_message: str) -> bool:
        return self._send(
            f"❗ <b>系统错误</b>\n<code>{error_message}</code>\n"
            f"<i>{datetime.utcnow().strftime('%H:%M UTC')}</i>"
        )
