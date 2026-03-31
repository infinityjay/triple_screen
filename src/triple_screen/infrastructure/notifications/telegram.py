from __future__ import annotations

import logging
from datetime import datetime

import requests

from triple_screen.config.schema import RiskConfig, TelegramConfig

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, settings: TelegramConfig, risk: RiskConfig) -> None:
        self.settings = settings
        self.risk = risk
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

    def format_signal_message(self, signal: dict) -> str:
        direction = signal["direction"]
        symbol = signal["symbol"]
        score = signal.get("signal_score", 0)
        weekly = signal["weekly"]
        daily = signal["daily"]
        hourly = signal["hourly"]
        exits = signal["exits"]

        dir_emoji = "🚀" if direction == "LONG" else "🔻"
        dir_label = "做多" if direction == "LONG" else "做空"
        hist_bar = self._bar(abs(weekly["histogram"]) * 1000, 5, length=8)
        rsi_bar = self._bar(daily["rsi"], 100, length=10)
        breakout_bar = self._bar(hourly.get("breakout_strength", 0), 1.0, length=8)
        pos_value = round(exits["position_size"] * exits["entry"], 2)

        if direction == "LONG":
            breakout_line = (
                f"N高：{hourly['high_n']:.2f}  Close：{hourly['close']:.2f}  突破：{hourly['close'] - hourly['high_n']:.2f}"
            )
        else:
            breakout_line = (
                f"N低：{hourly['low_n']:.2f}  Close：{hourly['close']:.2f}  跌破：{hourly['low_n'] - hourly['close']:.2f}"
            )

        return (
            f"{dir_emoji} <b>{symbol} · {dir_label}信号</b>\n"
            f"综合评分：{self._score_stars(score)} <code>{score:.1f}/10</code>\n"
            f"{'─' * 32}\n"
            f"<b>第一重 · 周线 MACD</b>\n"
            f"方向：<b>{weekly['trend']}</b>\n"
            f"MACD：{weekly['macd']:.4f}  Signal：{weekly['macd_signal']:.4f}\n"
            f"Histogram：<code>{weekly['histogram']:+.4f}</code>\n"
            f"强度条：[{hist_bar}]  连续确认：{weekly['confirmed_bars']} 根\n"
            f"{'─' * 32}\n"
            f"<b>第二重 · 日线 RSI</b>\n"
            f"RSI(14)：<code>{daily['rsi']:.1f}</code> ({daily['rsi_state']})\n"
            f"上根 RSI：{daily['rsi_prev']:.1f}\n"
            f"RSI 条：[{rsi_bar}] {daily['rsi']:.0f}\n"
            f"{'─' * 32}\n"
            f"<b>第三重 · 1小时突破</b>\n"
            f"{breakout_line}\n"
            f"突破强度：[{breakout_bar}] {hourly.get('breakout_strength', 0):.2f}xATR\n"
            f"ATR：<code>{hourly['atr']:.4f}</code>\n"
            f"{'─' * 32}\n"
            f"<b>交易建议</b>\n"
            f"入场：<code>{exits['entry']:.2f}</code>\n"
            f"SL(ATR)：<code>{exits['sl_atr']:.2f}</code>\n"
            f"SL(前K线)：<code>{exits['sl_prev_candle']:.2f}</code>\n"
            f"TP(固定RR)：<code>{exits['tp_fixed_rr']:.2f}</code>\n"
            f"推荐仓位：<b>{exits['position_size']:.0f} 股</b> ≈ ${pos_value:,.0f}\n"
            f"账户风险：{self.risk.account_risk_pct:.0%} / 最大持仓 {self.risk.max_hold_bars} 小时\n"
            f"{'─' * 32}\n"
            f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>"
        )

    def format_summary_message(self, signals: list[dict], scan_time_sec: float) -> str:
        if not signals:
            return (
                "🔍 <b>扫描完成 · 无信号</b>\n"
                f"<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>"
            )

        lines = [
            f"📋 <b>扫描完成 · 发现 {len(signals)} 个信号</b>\n",
            f"{'─' * 32}\n",
        ]
        for index, signal in enumerate(sorted(signals, key=lambda item: item["signal_score"], reverse=True)[:10], start=1):
            emoji = "🚀" if signal["direction"] == "LONG" else "🔻"
            lines.append(
                f"{index}. {emoji} <b>{signal['symbol']}</b> "
                f"评分 {signal['signal_score']:.1f} "
                f"入场 {signal['exits']['entry']:.2f} "
                f"SL {signal['exits']['sl_atr']:.2f} "
                f"TP {signal['exits']['tp_fixed_rr']:.2f}\n"
            )

        lines.append(f"\n<i>耗时 {scan_time_sec:.1f}s · {datetime.utcnow().strftime('%H:%M UTC')}</i>")
        return "".join(lines)

    def send_scan_start(self, symbol_count: int) -> bool:
        return self._send(
            "🔄 <b>开始扫描</b>\n"
            f"股票池：{symbol_count} 只\n"
            f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>"
        )

    def send_signal(self, signal: dict) -> bool:
        return self._send(self.format_signal_message(signal))

    def send_summary(self, signals: list[dict], scan_time_sec: float) -> bool:
        return self._send(self.format_summary_message(signals, scan_time_sec))

    def send_error(self, error_message: str) -> bool:
        return self._send(
            f"❗ <b>系统错误</b>\n<code>{error_message}</code>\n"
            f"<i>{datetime.utcnow().strftime('%H:%M UTC')}</i>"
        )
