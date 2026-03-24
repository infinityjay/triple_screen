# src/report_generator.py
# 生成 HTML 报表 + docs/index.html 导航首页

from pathlib import Path
from datetime import datetime
from config_loader import load_config

DOCS_DIR     = Path("/Users/jay/workspace/my_github/infinityjay.github.io/trading")
REPORTS_DIR  = DOCS_DIR / "reports"

SIGNAL_CONFIG = {
    "BUY":              {"label": "买入",     "color": "#15803d", "bg": "#f0fdf4", "border": "#86efac"},
    "SELL":             {"label": "卖出",     "color": "#b91c1c", "bg": "#fef2f2", "border": "#fca5a5"},
    "WATCH_LONG":       {"label": "关注做多", "color": "#b45309", "bg": "#fffbeb", "border": "#fcd34d"},
    "WATCH_SHORT":      {"label": "关注做空", "color": "#c2410c", "bg": "#fff7ed", "border": "#fdba74"},
    "CAUTION_LONG":     {"label": "谨慎多头", "color": "#6d28d9", "bg": "#f5f3ff", "border": "#c4b5fd"},
    "NEUTRAL":          {"label": "中性",     "color": "#4b5563", "bg": "#f9fafb", "border": "#d1d5db"},
    "INSUFFICIENT_DATA":{"label": "数据不足", "color": "#9ca3af", "bg": "#f9fafb", "border": "#e5e7eb"},
    "ERROR":            {"label": "错误",     "color": "#9ca3af", "bg": "#f9fafb", "border": "#e5e7eb"},
}

SIGNAL_PRIORITY = ["BUY", "SELL", "WATCH_LONG", "WATCH_SHORT",
                   "CAUTION_LONG", "NEUTRAL", "INSUFFICIENT_DATA", "ERROR"]

def _get_dirs():
    config = load_config()
    output = config.get("settings", {}).get("output_dir")
    if output:
        docs_dir = Path(output)
    else:
        docs_dir = Path(__file__).parent.parent / "docs"
    return docs_dir, docs_dir / "reports"

def _badge(signal: str) -> str:
    cfg = SIGNAL_CONFIG.get(signal, SIGNAL_CONFIG["NEUTRAL"])
    return (
        f'<span style="display:inline-block;padding:3px 11px;border-radius:99px;'
        f'font-size:12px;font-weight:600;letter-spacing:.3px;'
        f'color:{cfg["color"]};background:{cfg["bg"]};border:1px solid {cfg["border"]}">'
        f'{cfg["label"]}</span>'
    )


def _bool_icon(val) -> str:
    return "✅" if val else "❌"


def _pct_bar(val: float, low=30, high=70) -> str:
    """KD 值的迷你进度条"""
    color = "#ef4444" if val > high else ("#22c55e" if val < low else "#f59e0b")
    return (
        f'<div style="display:flex;align-items:center;gap:6px">'
        f'<div style="width:60px;height:6px;background:#e5e7eb;border-radius:3px;overflow:hidden">'
        f'<div style="width:{min(val,100):.0f}%;height:100%;background:{color};border-radius:3px"></div></div>'
        f'<span style="font-family:monospace;font-size:13px">{val:.1f}</span></div>'
    )


def generate_report(results: list, report_date: str, config: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    settings  = config.get("settings", {})
    title     = settings.get("report_title", "三重过滤信号")
    gen_time  = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 汇总统计
    signals   = [r.get("signal", "NEUTRAL") for r in results]
    buy_cnt   = signals.count("BUY")
    sell_cnt  = signals.count("SELL")
    watch_cnt = signals.count("WATCH_LONG") + signals.count("WATCH_SHORT")

    # 按 group 分组
    groups: dict = {}
    for r in results:
        groups.setdefault(r.get("group", "其他"), []).append(r)

    # ── 汇总卡片 ────────────────────────────────
    summary_html = f"""
    <div class="summary-row">
      <div class="card card-green"><div class="card-num">{buy_cnt}</div><div class="card-lbl">买入信号</div></div>
      <div class="card card-red"><div class="card-num">{sell_cnt}</div><div class="card-lbl">卖出信号</div></div>
      <div class="card card-amber"><div class="card-num">{watch_cnt}</div><div class="card-lbl">关注标的</div></div>
      <div class="card card-gray"><div class="card-num">{len(results)}</div><div class="card-lbl">监控总数</div></div>
    </div>"""

    # ── 分组表格 ────────────────────────────────
    tables_html = ""
    for group_name, items in groups.items():
        rows = ""
        for r in items:
            sig = r.get("signal", "NEUTRAL")
            if sig in ("ERROR", "INSUFFICIENT_DATA"):
                rows += f"""<tr>
                  <td class="ticker-cell"><span class="ticker">{r['ticker']}</span>
                    <span class="name">{r.get('name','')}</span></td>
                  <td>{_badge(sig)}</td>
                  <td colspan="7" style="color:#9ca3af;font-size:13px">{r.get('error','无详情')}</td>
                </tr>"""
                continue

            trend = r.get("weekly_trend", "")
            trend_html = (
                f'<span style="color:#15803d;font-weight:600">▲ UP</span>'
                if trend == "UP" else
                f'<span style="color:#b91c1c;font-weight:600">▼ DOWN</span>'
            )
            rows += f"""<tr>
              <td class="ticker-cell">
                <span class="ticker">{r['ticker']}</span>
                <span class="name">{r.get('name','')}</span>
              </td>
              <td>{_badge(sig)}</td>
              <td>{trend_html}</td>
              <td style="text-align:center">{_bool_icon(r.get('weekly_macd_bullish'))}</td>
              <td>{_pct_bar(r.get('daily_stoch_k', 50))}<span style="color:#9ca3af;font-size:11px;margin-left:2px">K</span></td>
              <td>{_pct_bar(r.get('daily_stoch_d', 50))}<span style="color:#9ca3af;font-size:11px;margin-left:2px">D</span></td>
              <td style="font-family:monospace">${r.get('last_close','')}</td>
              <td style="font-family:monospace;color:#6b7280">${r.get('daily_ema22','')}</td>
              <td style="text-align:center">{_bool_icon(r.get('price_above_ema22'))}</td>
            </tr>"""

        tables_html += f"""
        <div class="group-label">{group_name}</div>
        <div class="table-wrap">
        <table>
          <thead><tr>
            <th>股票</th>
            <th>信号</th>
            <th>周线趋势</th>
            <th>MACD看涨</th>
            <th>Stoch K</th>
            <th>Stoch D</th>
            <th>收盘价</th>
            <th>日线EMA{settings.get('daily_ema_period',22)}</th>
            <th>价格&gt;EMA</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
        </div>"""

    # ── 说明栏 ──────────────────────────────────
    legend_html = """
    <div class="legend">
      <div class="legend-title">信号说明（三重过滤规则）</div>
      <div class="legend-grid">
        <div><span style="color:#15803d;font-weight:600">买入</span>：周线趋势向上 + MACD看涨 + KD超卖后K上穿D</div>
        <div><span style="color:#b91c1c;font-weight:600">卖出</span>：周线趋势向下 + MACD看跌 + KD超买后K下穿D</div>
        <div><span style="color:#b45309;font-weight:600">关注做多</span>：趋势向上 + MACD看涨，等待回调入场</div>
        <div><span style="color:#c2410c;font-weight:600">关注做空</span>：趋势向下 + MACD看跌，等待反弹做空</div>
      </div>
    </div>"""

    # ── 完整 HTML ────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {report_date}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, "Helvetica Neue", "PingFang SC", Arial, sans-serif;
    background: #f1f5f9;
    color: #111827;
    padding: 20px 16px 60px;
    line-height: 1.5;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}

  /* Header */
  .header {{
    background: #fff;
    border-radius: 14px;
    padding: 22px 26px;
    margin-bottom: 20px;
    border: 1px solid #e2e8f0;
  }}
  .header h1 {{ font-size: 20px; font-weight: 700; color: #0f172a; }}
  .header .meta {{ color: #64748b; font-size: 13px; margin-top: 5px; }}
  .header .nav {{ margin-top: 12px; }}
  .header .nav a {{
    color: #3b82f6; text-decoration: none; font-size: 13px; font-weight: 500;
  }}
  .header .nav a:hover {{ text-decoration: underline; }}

  /* Summary cards */
  .summary-row {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 20px;
  }}
  .card {{
    background: #fff;
    border-radius: 12px;
    padding: 16px;
    border: 1px solid #e2e8f0;
    text-align: center;
  }}
  .card-num {{ font-size: 28px; font-weight: 700; line-height: 1; }}
  .card-lbl {{ font-size: 12px; color: #6b7280; margin-top: 4px; }}
  .card-green .card-num {{ color: #15803d; }}
  .card-red   .card-num {{ color: #b91c1c; }}
  .card-amber .card-num {{ color: #b45309; }}
  .card-gray  .card-num {{ color: #374151; }}

  /* Group label */
  .group-label {{
    font-size: 13px; font-weight: 600; color: #6b7280;
    text-transform: uppercase; letter-spacing: .5px;
    margin: 24px 0 8px;
  }}

  /* Table */
  .table-wrap {{
    background: #fff;
    border-radius: 12px;
    border: 1px solid #e2e8f0;
    overflow: hidden;
    overflow-x: auto;
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; min-width: 720px; }}
  th {{
    background: #f8fafc;
    padding: 10px 14px;
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    color: #64748b;
    border-bottom: 1px solid #e2e8f0;
    white-space: nowrap;
  }}
  td {{
    padding: 12px 14px;
    border-bottom: 1px solid #f1f5f9;
    vertical-align: middle;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8fafc; }}

  .ticker-cell {{ min-width: 120px; }}
  .ticker {{ font-weight: 700; font-size: 14px; display: block; }}
  .name {{ font-size: 12px; color: #94a3b8; display: block; margin-top: 1px; }}

  /* Legend */
  .legend {{
    background: #fff;
    border-radius: 12px;
    border: 1px solid #e2e8f0;
    padding: 18px 22px;
    margin-top: 24px;
  }}
  .legend-title {{ font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 10px; }}
  .legend-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px 24px;
    font-size: 13px;
    color: #4b5563;
  }}

  /* Mobile */
  @media (max-width: 640px) {{
    .summary-row {{ grid-template-columns: repeat(2, 1fr); }}
    .legend-grid {{ grid-template-columns: 1fr; }}
    body {{ padding: 12px 10px 40px; }}
  }}
</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <h1>{title}</h1>
    <div class="meta">
      报表日期：{report_date} &nbsp;|&nbsp; 生成时间：{gen_time}
      &nbsp;|&nbsp; 基于 Alexander Elder《以交易为生》三重过滤系统
    </div>
    <div class="nav"><a href="../index.html">← 返回历史报表列表</a></div>
  </div>

  {summary_html}
  {tables_html}
  {legend_html}

</div>
</body>
</html>"""

    path = REPORTS_DIR / f"{report_date}.html"
    path.write_text(html, encoding="utf-8")
    return path


def generate_index():
    """扫描所有历史报表，生成 docs/index.html 导航首页"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_files = sorted(REPORTS_DIR.glob("*.html"), reverse=True)
    count = len(report_files)

    items_html = ""
    for i, f in enumerate(report_files):
        date_str = f.stem
        latest_badge = (
            '<span style="font-size:11px;font-weight:600;color:#15803d;'
            'background:#f0fdf4;border:1px solid #86efac;'
            'padding:1px 7px;border-radius:99px;margin-left:8px">最新</span>'
            if i == 0 else ""
        )
        items_html += (
            f'<li><a href="reports/{f.name}">{date_str}</a>{latest_badge}</li>\n'
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>三重过滤 — 历史报表</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, "Helvetica Neue", "PingFang SC", Arial, sans-serif;
    background: #f1f5f9;
    color: #111827;
    min-height: 100vh;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding: 60px 20px;
  }}
  .card {{
    background: #fff;
    border-radius: 16px;
    border: 1px solid #e2e8f0;
    padding: 36px 40px;
    width: 100%;
    max-width: 480px;
  }}
  h1 {{ font-size: 22px; font-weight: 700; color: #0f172a; }}
  .sub {{ color: #64748b; font-size: 14px; margin-top: 6px; margin-bottom: 28px; }}
  ul {{ list-style: none; }}
  li {{ margin-bottom: 8px; border-bottom: 1px solid #f1f5f9; padding-bottom: 8px; }}
  li:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
  a {{
    color: #1d4ed8; text-decoration: none;
    font-size: 15px; font-weight: 500;
  }}
  a:hover {{ text-decoration: underline; }}
  .empty {{ color: #94a3b8; font-size: 14px; }}
</style>
</head>
<body>
<div class="card">
  <h1>三重过滤交易信号</h1>
  <p class="sub">每个交易日收盘后自动更新 · 共 {count} 份报表</p>
  <ul>
    {items_html if items_html else '<li class="empty">暂无报表，请先运行 daily_run.py</li>'}
  </ul>
</div>
</body>
</html>"""

    index_path = DOCS_DIR / "index.html"
    index_path.write_text(html, encoding="utf-8")
    print(f"  首页已更新: {index_path}（共 {count} 份报表）")
