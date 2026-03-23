#!/usr/bin/env python3
# src/daily_run.py
# ============================================================
# 每日执行入口，由 cron 在收盘后调用
#
# 用法：
#   python src/daily_run.py           # 每日正常运行
#   python src/daily_run.py --init    # 首次初始化历史数据
#   python src/daily_run.py --no-push # 运行但不推送 GitHub
# ============================================================

import sys
import logging
from pathlib import Path
from datetime import datetime

# 确保 src/ 在 import 路径中
sys.path.insert(0, str(Path(__file__).parent))

from config_loader import load_config, get_watchlist
from data_fetcher import fetch_history, fetch_weekly, fetch_latest, fetch_latest_weekly
from storage import init_db, save_df, load_df, ticker_exists
from indicators import run_triple_screen
from report_generator import generate_report, generate_index
from git_push import push_reports

# ── 日志配置 ────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "run.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
def initialize(force: bool = False):
    """
    首次运行，或新增了 ticker 时自动补充历史数据。
    force=True 时强制重新下载所有 ticker。
    """
    init_db()
    config   = load_config()
    settings = config["settings"]
    watchlist = config["watchlist"]

    log.info("── 初始化检查 ──────────────────────────────")
    for item in watchlist:
        ticker = item["ticker"]
        if not force and ticker_exists(ticker):
            log.info(f"  {ticker:6s} 已有历史数据，跳过")
            continue
        log.info(f"  {ticker:6s} 下载历史数据（{settings['history_period']}）...")
        try:
            save_df(ticker, fetch_history(ticker, settings["history_period"]), "daily")
            save_df(ticker, fetch_weekly(ticker,  settings["history_period"]), "weekly")
            log.info(f"  {ticker:6s} 初始化完成")
        except Exception as e:
            log.error(f"  {ticker:6s} 初始化失败: {e}")

    log.info("初始化完成\n")


# ────────────────────────────────────────────────────────────
def daily_run(push: bool = True):
    """每日任务主流程"""
    import pytz
    config   = load_config()          # 每次重新读取，watchlist 变更立即生效
    watchlist = config["watchlist"]
    settings  = config["settings"]

    tz       = pytz.timezone(settings["timezone"])
    run_date = datetime.now(tz).strftime("%Y-%m-%d")

    log.info(f"══════════════════════════════════════════")
    log.info(f"  开始执行  {run_date}")
    log.info(f"══════════════════════════════════════════")

    # 新增 ticker 自动补历史数据
    initialize(force=False)

    results = []
    for item in watchlist:
        ticker = item["ticker"]
        log.info(f"  处理 {ticker} ({item.get('name','')})")
        try:
            # 增量更新日线和周线
            save_df(ticker, fetch_latest(ticker),        "daily")
            save_df(ticker, fetch_latest_weekly(ticker), "weekly")

            daily_df  = load_df(ticker, "daily")
            weekly_df = load_df(ticker, "weekly")

            result = run_triple_screen(daily_df, weekly_df, settings)
            result.update({
                "ticker": ticker,
                "name":   item.get("name", ticker),
                "group":  item.get("group", "其他"),
            })
            results.append(result)
            log.info(f"    → 信号: {result['signal']:15s} "
                     f"收盘: ${result.get('last_close','N/A')}")

        except Exception as e:
            log.error(f"    → ERROR: {e}")
            results.append({
                "ticker": ticker,
                "name":   item.get("name", ticker),
                "group":  item.get("group", "其他"),
                "signal": "ERROR",
                "error":  str(e),
            })

    # 生成报表
    log.info("─── 生成报表 ────────────────────────────")
    report_path = generate_report(results, run_date, config)
    generate_index()
    log.info(f"  报表路径: {report_path}")

    # 推送到 GitHub
    if push:
        log.info("─── 推送 GitHub ─────────────────────────")
        push_reports(run_date)

    log.info(f"══ 完成 {run_date} ══\n")
    return results


# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--init" in args:
        initialize(force=True)
    else:
        no_push = "--no-push" in args
        daily_run(push=not no_push)
