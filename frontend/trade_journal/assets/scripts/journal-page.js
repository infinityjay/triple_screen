import {
  APP_CONFIG,
  apiRequest,
  calculateGrossPnl,
  calculateNetPnl,
  downloadJson,
  ensureApiReady,
  escapeHtml,
  formatCurrency,
  formatDateLabel,
  formatInputNumber,
  formatNumber,
  formatPercent,
  formatShares,
  getCommissionValue,
  getDirectionLabel,
  getLocalDateStamp,
  getLocalMonthStamp,
  getRecommendedShares,
  getRiskPerShare,
  getStatusMeta,
  getStopStatus,
  getTargetPrice,
  getTradeAgeInDays,
  getTradeAnchorDate,
  getTradeBuyMonth,
  getTradeCompletionGaps,
  getTradeNetPnl,
  getTradeSellMonth,
  getTradeTargetPct,
  getTradeTargetPrice,
  getTradeUsedStop,
  hasNumberValue,
  hasPartialSellInfo,
  hasTextValue,
  isTradeClosed,
  isTradeOpenAtMonthEnd,
  isTradeRelevantToMonth,
  normalizeDirection,
  normalizeSettings,
  parseNumberValue,
  readStoredJson,
  renderConnectionStatus,
  safeStorageSet,
  setScreenState,
  settingsRowToState,
  settingsStateToRow,
  syncShell,
} from "./shared.js";

const REVIEW_TEMPLATES = {
  plan: [
    "交易计划",
    "1. 周 / 日 / 小时结构是否一致：",
    "2. 具体入场触发条件：",
    "3. 初始止损与失效条件：",
    "4. 目标位 / 减仓计划：",
    "5. 今天最需要提醒自己的执行点：",
  ].join("\n"),
  review: [
    "交易复盘",
    "1. 这笔交易最好的地方：",
    "2. 最大的问题：",
    "3. 有没有按计划执行：",
    "4. 情绪是否干扰了决策：",
    "5. 下次遇到类似 setup 要怎么做：",
  ].join("\n"),
};

const state = {
  trades: [],
  settings: normalizeSettings(readStoredJson("tradeSettings", {})),
  editingId: null,
  captureInitialStop: null,
  captureSuggestedStop: null,
  activeSection: "overview",
  settingsSaveTimer: null,
};

const MONTH_INPUT_IDS = ["heroMonthPicker", "journalMonthPicker", "statsMonthPicker"];
const FORM_INPUT_IDS = [
  "f_stock",
  "f_direction",
  "f_buyDate",
  "f_buyPrice",
  "f_initialStopLoss",
  "f_stopLoss",
  "f_suggestedStopLoss",
  "f_shares",
  "f_stopReason",
  "f_targetPct",
  "f_targetPrice",
  "f_buyComm",
  "f_chanHigh",
  "f_chanLow",
  "f_dayHigh",
  "f_dayLow",
  "f_sellDate",
  "f_sellPrice",
  "f_sellComm",
  "f_sellHigh",
  "f_sellLow",
  "f_sellReason",
  "f_review",
];

function $(id) {
  return document.getElementById(id);
}

function showAlert(containerId, message, tone = "warn") {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = message ? `<div class="alert ${tone}">${escapeHtml(message)}</div>` : "";
}

function showGlobalAlert(message, tone = "warn") {
  showAlert("globalAlertZone", message, tone);
}

function getSettings() {
  return normalizeSettings(state.settings);
}

function cacheSettings() {
  safeStorageSet("tradeSettings", JSON.stringify(state.settings));
}

function getCurrentMonth() {
  return getSettings().month;
}

function getTradesSource(includeCapturePreview = false) {
  if (!includeCapturePreview) return state.trades;

  const preview = getCapturePreviewTrade();
  if (!preview) return state.trades;

  const trades = state.trades.slice();
  const index = trades.findIndex((trade) => String(trade.id) === String(preview.id));
  if (index >= 0) trades[index] = preview;
  else trades.unshift(preview);
  return trades;
}

function getTradesForMonth(month, includeCapturePreview = false) {
  return getTradesSource(includeCapturePreview).filter((trade) => isTradeRelevantToMonth(trade, month));
}

function getClosedTradesForMonth(month, includeCapturePreview = false) {
  return getTradesForMonth(month, includeCapturePreview).filter((trade) => getTradeSellMonth(trade) === month);
}

function getOpenTradesForMonth(month, includeCapturePreview = false) {
  return getTradesForMonth(month, includeCapturePreview).filter((trade) => isTradeOpenAtMonthEnd(trade, month));
}

function getCapturePreviewTrade() {
  const stock = $("f_stock")?.value?.trim()?.toUpperCase() || "";
  const buyPrice = getNumberInputValue("f_buyPrice");
  const stopLoss = getNumberInputValue("f_stopLoss");
  const shares = getNumberInputValue("f_shares");
  const buyDate = $("f_buyDate")?.value || null;
  const sellPrice = getNumberInputValue("f_sellPrice");
  const sellDate = $("f_sellDate")?.value || null;
  const direction = normalizeDirection($("f_direction")?.value);

  if (!stock || buyPrice === null || stopLoss === null || shares === null || !buyDate) {
    return null;
  }

  const existing = state.editingId
    ? state.trades.find((trade) => String(trade.id) === String(state.editingId)) || {}
    : {};

  return {
    ...existing,
    id: state.editingId || "__capture_preview__",
    stock,
    direction,
    buy_price: buyPrice,
    stop_loss: stopLoss,
    initial_stop_loss: state.captureInitialStop ?? stopLoss,
    suggested_stop_loss: state.captureSuggestedStop,
    shares,
    buy_date: buyDate,
    sell_price: sellPrice,
    sell_date: sellDate,
    created_at: existing.created_at || new Date().toISOString(),
    used_stop: null,
  };
}

function getMonthClosedRiskUsed(month, includeCapturePreview = false) {
  return getClosedTradesForMonth(month, includeCapturePreview).reduce((sum, trade) => sum + getTradeUsedStop(trade), 0);
}

function getMonthOpenRisk(month, includeCapturePreview = false) {
  const trades = getOpenTradesForMonth(month, includeCapturePreview);
  return trades.reduce((sum, trade) => sum + getTradeUsedStop(trade), 0);
}

function getMonthStopBudgetUsed(month, includeCapturePreview = false) {
  return getMonthClosedRiskUsed(month, includeCapturePreview) + getMonthOpenRisk(month, includeCapturePreview);
}

function getFilteredTrades() {
  const month = getCurrentMonth();
  const status = $("journalStatusFilter")?.value || "all";
  const query = ($("journalSearchInput")?.value || "").trim().toLowerCase();
  return getTradesForMonth(month).filter((trade) => {
    const pnl = getTradeNetPnl(trade);
    const gaps = getTradeCompletionGaps(trade);
    const searchText = [
      trade.stock,
      getDirectionLabel(trade.direction),
      trade.stop_reason,
      trade.sell_reason,
      trade.review,
      gaps.join(" "),
    ]
      .join(" ")
      .toLowerCase();

    if (status === "open" && isTradeClosed(trade)) return false;
    if (status === "closed" && !isTradeClosed(trade)) return false;
    if (status === "win" && !(pnl !== null && pnl >= 0)) return false;
    if (status === "loss" && !(pnl !== null && pnl < 0)) return false;
    if (status === "planned" && gaps.length > 0) return false;
    if (query && !searchText.includes(query)) return false;
    return true;
  });
}

function getOverdueIncompleteTrades(months = 3) {
  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setMonth(cutoff.getMonth() - months);

  return state.trades
    .filter((trade) => {
      const anchor = getTradeAnchorDate(trade);
      return anchor && anchor <= cutoff && getTradeCompletionGaps(trade).length > 0;
    })
    .sort((a, b) => {
      const left = getTradeAnchorDate(a)?.getTime() || 0;
      const right = getTradeAnchorDate(b)?.getTime() || 0;
      return left - right;
    });
}

function getMonthlyCompletionRate(month) {
  const trades = getTradesForMonth(month);
  if (!trades.length) return null;
  const completeCount = trades.filter((trade) => getTradeCompletionGaps(trade).length === 0).length;
  return (completeCount / trades.length) * 100;
}

function getRiskNumbers(includeCapturePreview = false) {
  const settings = getSettings();
  const singleStop = settings.total * (settings.singleStop / 100);
  const monthBudget = settings.total * (settings.monthStop / 100);
  const used = getMonthStopBudgetUsed(settings.month, includeCapturePreview);
  const remaining = monthBudget - used;
  const pct = monthBudget > 0 ? (used / monthBudget) * 100 : 0;
  return { singleStop, monthBudget, used, remaining, pct };
}

function truncateText(value, length = 100) {
  const text = String(value || "").trim();
  if (!text) return "—";
  return text.length > length ? `${text.slice(0, length).trim()}...` : text;
}

function getTradeInitialStop(trade) {
  return parseNumberValue(trade?.initial_stop_loss) ?? parseNumberValue(trade?.stop_loss);
}

function getTradeCurrentStop(trade) {
  return parseNumberValue(trade?.stop_loss);
}

function getTradeSuggestedStop(trade) {
  return parseNumberValue(trade?.suggested_stop_loss);
}

function setSection(section) {
  state.activeSection = section;
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${section}`);
  });
  document.querySelectorAll("[data-section-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.sectionTab === section);
  });
}

function syncMonthInputs() {
  const month = getCurrentMonth();
  MONTH_INPUT_IDS.forEach((id) => {
    const input = $(id);
    if (input) input.value = month;
  });
  if ($("s_month")) $("s_month").value = month;
}

function setReportMonth(month, persist = true) {
  if (!month) return;
  state.settings.month = month;
  cacheSettings();
  syncMonthInputs();
  refreshAll();
  if (persist) scheduleSettingsSave();
}

function renderSummary(includeCapturePreview = false) {
  const settings = getSettings();
  const month = settings.month;
  const openTrades = getOpenTradesForMonth(month, includeCapturePreview);
  const closedTrades = getClosedTradesForMonth(month, includeCapturePreview);
  const wins = closedTrades.filter((trade) => (getTradeNetPnl(trade) || 0) >= 0);
  const netClosed = closedTrades.reduce((sum, trade) => sum + (getTradeNetPnl(trade) || 0), 0);
  const completionRate = getMonthlyCompletionRate(month);
  const overdue = getOverdueIncompleteTrades();
  const { singleStop, monthBudget, used, remaining, pct } = getRiskNumbers(includeCapturePreview);
  const closedRiskUsed = getMonthClosedRiskUsed(month, includeCapturePreview);
  const openRiskUsed = getMonthOpenRisk(month, includeCapturePreview);

  $("summaryTotal").textContent = settings.total ? formatCurrency(settings.total, 0) : "未设置";
  $("summarySingleStop").textContent = settings.total ? formatCurrency(singleStop, 2) : "—";
  $("summaryMonthBudget").textContent = settings.total ? formatCurrency(monthBudget, 2) : "—";
  $("summaryRemaining").textContent = settings.total ? formatCurrency(remaining, 2) : "—";
  $("summaryClosedResult").textContent = closedTrades.length ? formatCurrency(netClosed, 2) : "—";
  $("summaryClosedResult").className = `summary-value ${netClosed >= 0 ? "accent-safe" : "accent-danger"}`;
  $("summaryClosedResultSub").textContent = closedTrades.length
    ? `${closedTrades.length} 笔已结 · ${wins.length} 胜 / ${closedTrades.length - wins.length} 负`
    : "本月暂无已结交易";
  $("summaryOpenCount").textContent = String(openTrades.length);
  $("summaryUsedPct").textContent = formatPercent(pct, 0);
  $("summaryUsedText").textContent = `已结亏损占用 ${formatCurrency(closedRiskUsed, 2)} + 开放风险 ${formatCurrency(openRiskUsed, 2)}`;
  $("summaryCompleteness").textContent = completionRate === null ? "暂无样本" : formatPercent(completionRate, 0);
  $("summaryCompletenessSub").textContent = overdue.length
    ? `${overdue.length} 笔超过 3 个月仍未补全`
    : "本月和历史记录都比较完整";
  $("summarySingleStopSub").textContent = settings.total
    ? `${formatPercent(settings.singleStop, 1)} 规则已启用`
    : "先在设置里填总资金";
  $("summaryMonthBudgetSub").textContent = settings.total
    ? `${formatPercent(settings.monthStop, 1)} 月度止损规则已启用`
    : "月度预算尚未设定";
  $("summaryRemainingSub").textContent = `已用 ${formatCurrency(used, 2)}，剩余 ${formatCurrency(remaining, 2)}`;
  $("summaryOpenCountSub").textContent = openTrades.length
    ? `其中 ${openTrades.filter((trade) => normalizeDirection(trade.direction) === "short").length} 笔做空`
    : "当前没有月末持仓";
  $("summaryRiskFill").style.width = `${Math.max(0, Math.min(100, pct))}%`;

  $("monthHeadline").textContent = `当前查看月份：${month}`;
  $("monthHeadlineBody").textContent = `${closedTrades.length} 笔已结，${wins.length} 笔盈利，${openTrades.length} 笔持仓仍占用风险预算。`;
  $("journalMonthCurrent").textContent = `当前查看月份：${month}`;
  $("statsMonthCurrent").textContent = `当前查看月份：${month}`;

  const notes = [];
  if (!settings.total) notes.push("先在设置里填写总资金和风险比例，系统才能给出仓位建议。");
  if (pct >= 100) notes.push("本月风险预算已用尽，优先降低新交易频率。");
  else if (pct >= 75) notes.push("本月风险预算接近上限，新仓要更严格筛选。");
  if (overdue.length) notes.push(`有 ${overdue.length} 笔旧交易还没补完整，统计结论会受影响。`);
  if (!closedTrades.length) notes.push("本月还没有已结交易，先聚焦执行质量和记录完整度。");
  $("heroNotes").innerHTML = notes.length
    ? notes.map((item) => `<div class="status-pill">${escapeHtml(item)}</div>`).join("")
    : `<div class="status-pill">本月记录完整，继续按节奏维护交易日志。</div>`;
}

function renderOverview() {
  const overdue = getOverdueIncompleteTrades();
  const openTrades = getOpenTradesForMonth(getCurrentMonth());
  const recentTrades = [...state.trades]
    .slice()
    .sort((a, b) => (getTradeAnchorDate(b)?.getTime() || 0) - (getTradeAnchorDate(a)?.getTime() || 0))
    .slice(0, 5);

  $("reminderSummary").innerHTML = overdue.length
    ? `超过 3 个月仍待补的交易共有 <strong>${overdue.length}</strong> 笔，建议先补交易计划、复盘和价格区间。`
    : "没有超过 3 个月仍未补完整的记录。";

  $("reminderList").innerHTML = overdue.length
    ? overdue
        .slice(0, 6)
        .map((trade) => {
          const gaps = getTradeCompletionGaps(trade);
          return `
            <div class="reminder-item">
              <strong>${escapeHtml(trade.stock || "—")} · ${escapeHtml(getDirectionLabel(trade.direction))}</strong>
              <p>${formatDateLabel(trade.buy_date || trade.created_at)} 建立，距今 ${getTradeAgeInDays(trade) || 0} 天，待补：${escapeHtml(gaps.join("、"))}</p>
              <div class="btn-row" style="margin-top:12px">
                <button class="btn btn-secondary" type="button" data-edit-trade="${escapeHtml(String(trade.id))}">继续补录</button>
              </div>
            </div>
          `;
        })
        .join("")
    : `<div class="empty-state">目前没有长期未补完整的交易。</div>`;

  const focusItems = openTrades.length ? openTrades.slice(0, 5) : recentTrades;
  $("focusList").innerHTML = focusItems.length
    ? focusItems
        .map((trade) => {
          const pnl = getTradeNetPnl(trade);
          const meta = getStatusMeta(trade);
          return `
            <div class="focus-item">
              <strong>${escapeHtml(trade.stock || "—")} · ${escapeHtml(getDirectionLabel(trade.direction))}</strong>
              <p>${escapeHtml(meta.label)} · 入场 ${formatCurrency(trade.buy_price, 3)} · 股数 ${formatShares(trade.shares)} · ${pnl === null ? "等待平仓结果" : `净结果 ${formatCurrency(pnl, 2)}`}</p>
              <div class="btn-row" style="margin-top:12px">
                <button class="btn btn-secondary" type="button" data-edit-trade="${escapeHtml(String(trade.id))}">编辑</button>
              </div>
            </div>
          `;
        })
        .join("")
    : `<div class="empty-state">还没有交易，先录入第一笔。</div>`;

  const month = getCurrentMonth();
  const closedTrades = getClosedTradesForMonth(month);
  const insights = [];
  const { pct, remaining } = getRiskNumbers();
  if (pct >= 100) insights.push(["本月风险预算已满", "暂停新增仓位，优先处理现有持仓和复盘。"]);
  else if (pct >= 75) insights.push(["本月风险预算偏高", `剩余额度只剩 ${formatCurrency(remaining, 2)}，新仓要缩量。`]);
  if (openTrades.length) insights.push(["先盯住仍在持仓的仓位", `当前有 ${openTrades.length} 笔持仓，优先保证止损和跟踪记录及时更新。`]);
  if (!closedTrades.length) insights.push(["本月样本偏少", "已结交易太少时，不要过度解读胜率和净结果。"]);
  if (!insights.length) insights.push(["节奏正常", "风险、样本数和记录完整度都在可接受范围内。"]);
  $("monthInsights").innerHTML = insights
    .map(([title, body]) => `<div class="insight-item"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(body)}</p></div>`)
    .join("");
}

function renderJournalRail(list) {
  if (!list.length) {
    $("journalRailContainer").innerHTML = `<div class="empty-state">当前筛选条件下没有交易卡片。</div>`;
    return;
  }

  const cards = list
    .map((trade) => {
      const meta = getStatusMeta(trade);
      const pnl = getTradeNetPnl(trade);
      const gaps = getTradeCompletionGaps(trade);
      const target = getTradeTargetPrice(trade);
      const usedStop = getTradeUsedStop(trade);
      const initialStop = getTradeInitialStop(trade);
      const currentStop = getTradeCurrentStop(trade);
      const primaryNote = trade.stop_reason || trade.sell_reason || trade.review || "这笔交易还没有补充说明。";

      return `
        <article class="journal-rail-item">
          <div class="journal-rail-top">
            <div>
              <h3>${escapeHtml(trade.stock || "—")}</h3>
              <p>${escapeHtml(getDirectionLabel(trade.direction))} · ${escapeHtml(formatDateLabel(trade.buy_date || trade.created_at))}</p>
            </div>
            <span class="badge badge-${meta.tone}">${escapeHtml(meta.label)}</span>
          </div>
          <div class="journal-rail-metrics">
            <div>
              <span>入场</span>
              <strong>${formatCurrency(trade.buy_price, 3)}</strong>
            </div>
            <div>
              <span>风险占用</span>
              <strong>${formatCurrency(usedStop, 2)}</strong>
            </div>
            <div>
              <span>目标</span>
              <strong>${formatCurrency(target, 3)}</strong>
            </div>
            <div>
              <span>当前保护止损</span>
              <strong>${formatCurrency(currentStop, 3)}</strong>
            </div>
            <div>
              <span>结果</span>
              <strong class="${pnl === null ? "" : pnl >= 0 ? "tone-safe" : "tone-danger"}">${pnl === null ? "持仓中" : formatCurrency(pnl, 2)}</strong>
            </div>
          </div>
          <div class="journal-rail-body">
            <div class="journal-rail-block">
              <strong>计划 / 原因</strong>
              <p>${escapeHtml(truncateText(primaryNote, 120))}</p>
            </div>
            <div class="journal-rail-footer">
              <span>${gaps.length ? `待补 ${escapeHtml(gaps.join("、"))}` : "记录已完整"}</span>
              <span>${formatShares(trade.shares)} · 初始止损 ${formatCurrency(initialStop, 3)} · 当前保护止损 ${formatCurrency(currentStop, 3)}</span>
            </div>
          </div>
        </article>
      `;
    })
    .join("");

  $("journalRailContainer").innerHTML = `
    <div class="journal-rail-shell">
      <div class="journal-rail">${cards}</div>
    </div>
  `;
}

function renderJournalTable(list) {
  if (!list.length) {
    $("journalTableContainer").innerHTML = `<div class="empty-state">当前筛选条件下没有交易记录。</div>`;
    return;
  }

  const rows = list
    .map((trade) => {
      const meta = getStatusMeta(trade);
      const pnl = getTradeNetPnl(trade);
      const gaps = getTradeCompletionGaps(trade);
      const initialStop = getTradeInitialStop(trade);
      const currentStop = getTradeCurrentStop(trade);
      return `
        <tr>
          <td class="symbol-cell">
            <strong>${escapeHtml(trade.stock || "—")}</strong>
            <span>${escapeHtml(getDirectionLabel(trade.direction))}</span>
          </td>
          <td><span class="badge badge-${meta.tone}">${escapeHtml(meta.label)}</span></td>
          <td>${formatDateLabel(trade.buy_date || trade.created_at)}</td>
          <td>${formatCurrency(trade.buy_price, 3)}</td>
          <td>${formatShares(trade.shares)}</td>
          <td>${formatCurrency(initialStop, 3)}</td>
          <td>${formatCurrency(currentStop, 3)}</td>
          <td>${formatCurrency(getTradeUsedStop(trade), 2)}</td>
          <td>${formatCurrency(getTradeTargetPrice(trade), 3)}</td>
          <td class="${pnl === null ? "" : pnl >= 0 ? "tone-safe" : "tone-danger"}">${pnl === null ? "持仓中" : formatCurrency(pnl, 2)}</td>
          <td class="reason-cell">${escapeHtml(trade.stop_reason || "—")}</td>
          <td class="reason-cell">${escapeHtml(trade.sell_reason || "—")}</td>
          <td class="reason-cell">${escapeHtml(gaps.length ? gaps.join("、") : "已完整")}</td>
          <td class="reason-cell">${escapeHtml((trade.review || "").slice(0, 120) || "—")}</td>
          <td>
            <div class="inline-actions">
              <button class="btn btn-secondary" type="button" data-edit-trade="${escapeHtml(String(trade.id))}">编辑</button>
              <button class="btn btn-secondary" type="button" data-delete-trade="${escapeHtml(String(trade.id))}">删除</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  $("journalTableContainer").innerHTML = `
    <div class="list-table-wrap compact-ledger-scroll">
      <table class="journal-ledger-table">
        <thead>
          <tr>
            <th>标的</th>
            <th>状态</th>
            <th>入场日期</th>
            <th>入场价</th>
            <th>股数</th>
            <th>初始止损价</th>
            <th>当前保护止损</th>
            <th>占用风险</th>
            <th>目标价</th>
            <th>净结果</th>
            <th>交易计划</th>
            <th>平仓原因</th>
            <th>待补字段</th>
            <th>复盘摘要</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderJournal() {
  const list = getFilteredTrades();
  $("journalCountLabel").textContent = `${list.length} 笔`;
  renderJournalRail(list);
  renderJournalTable(list);
}

function renderStats() {
  const month = getCurrentMonth();
  const all = getTradesForMonth(month);
  const closed = getClosedTradesForMonth(month);
  const open = getOpenTradesForMonth(month);
  const net = closed.reduce((sum, trade) => sum + (getTradeNetPnl(trade) || 0), 0);
  const wins = closed.filter((trade) => (getTradeNetPnl(trade) || 0) >= 0);
  const losses = closed.filter((trade) => (getTradeNetPnl(trade) || 0) < 0);
  const avgWin = wins.length ? wins.reduce((sum, trade) => sum + (getTradeNetPnl(trade) || 0), 0) / wins.length : null;
  const avgLoss = losses.length ? losses.reduce((sum, trade) => sum + Math.abs(getTradeNetPnl(trade) || 0), 0) / losses.length : null;
  const completionRate = getMonthlyCompletionRate(month);
  const risk = getRiskNumbers();

  let leadTitle = "本月结论";
  let leadBody = "继续保持记录质量，先看风险，再看结果。";
  if (!closed.length) {
    leadTitle = "样本不足，先看执行";
    leadBody = "本月还没有足够的已结交易样本，先确保计划、止损和复盘记录都完整。";
  } else if (net < 0) {
    leadTitle = "净结果偏弱，先收紧风险";
    leadBody = "本月已结交易净结果为负，优先复盘亏损交易的执行偏差，而不是急着扩大样本。";
  } else if (risk.pct >= 75) {
    leadTitle = "结果尚可，但风险已偏高";
    leadBody = "月度预算使用率偏高，新仓要继续收紧，把注意力放在高质量 setup 上。";
  } else if ((completionRate || 0) < 70) {
    leadTitle = "结果先放一边，先补数据";
    leadBody = "记录完整度偏低会直接影响统计质量，先把旧交易补全。";
  }

  $("statsLeadTitle").textContent = leadTitle;
  $("statsLeadBody").textContent = leadBody;
  $("statsLeadPills").innerHTML = [
    `<div class="stat-pill">已结 ${closed.length} 笔</div>`,
    `<div class="stat-pill">持仓 ${open.length} 笔</div>`,
    `<div class="stat-pill">胜率 ${closed.length ? formatPercent((wins.length / closed.length) * 100, 0) : "—"}</div>`,
    `<div class="stat-pill">完整度 ${completionRate === null ? "—" : formatPercent(completionRate, 0)}</div>`,
  ].join("");

  $("statsGrid").innerHTML = [
    ["净结果", closed.length ? formatCurrency(net, 2) : "—"],
    ["胜率", closed.length ? formatPercent((wins.length / closed.length) * 100, 0) : "—"],
    ["平均盈利", avgWin === null ? "—" : formatCurrency(avgWin, 2)],
    ["平均亏损", avgLoss === null ? "—" : formatCurrency(avgLoss, 2)],
    ["月度风险已用", formatPercent(risk.pct, 0)],
    ["开放风险", formatCurrency(getMonthOpenRisk(month), 2)],
    ["已记录交易", String(all.length)],
    ["完整记录", completionRate === null ? "—" : formatPercent(completionRate, 0)],
  ]
    .map(
      ([label, value]) => `
        <div class="summary-card" style="padding:16px">
          <div class="summary-label">${escapeHtml(label)}</div>
          <div class="summary-value" style="font-size:26px">${escapeHtml(value)}</div>
        </div>
      `
    )
    .join("");

  const suggestions = [];
  if (risk.pct >= 100) suggestions.push(["暂停新仓", "本月预算已经耗尽，优先处理当前持仓和复盘。"]);
  if (losses.length > wins.length && closed.length >= 4) suggestions.push(["复盘亏损共性", "检查是否总在同一类 setup 或同一执行环节上出错。"]);
  if ((completionRate || 0) < 70) suggestions.push(["补齐旧记录", "先把未填写的计划、平仓原因和复盘补完，再看统计。"]);
  if (!suggestions.length) suggestions.push(["延续当前流程", "继续按照现在的节奏维护交易日志，并关注持仓止损更新。"]);

  $("statsNarrative").innerHTML = suggestions
    .slice(0, 3)
    .map(([title, body]) => `<div class="insight-item"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(body)}</p></div>`)
    .join("");
}

function updateCaptureHeader() {
  const editing = state.editingId !== null;
  $("captureTitle").textContent = editing ? "编辑交易" : "录入新交易";
  $("captureSubtitle").textContent = editing
    ? "你正在修改一笔已有交易，保存后会直接更新 SQLite。"
    : "录入新交易时，系统会实时计算风险和目标位。";
  $("captureCancelBtn").classList.toggle("hidden", !editing);
  $("captureSaveBtn").textContent = editing ? "保存修改" : "保存交易";
}

function clearCaptureForm() {
  FORM_INPUT_IDS.forEach((id) => {
    const input = $(id);
    if (input) input.value = "";
  });
  $("f_direction").value = "long";
  $("f_buyDate").value = getLocalDateStamp();
  $("f_buyComm").value = "1";
  $("f_sellComm").value = "";
  state.editingId = null;
  state.captureInitialStop = null;
  state.captureSuggestedStop = null;
  updateCaptureHeader();
  computeCapture();
  showAlert("captureAlert", "");
}

function populateCaptureForm(trade) {
  state.editingId = String(trade.id);
  $("f_stock").value = trade.stock || "";
  $("f_direction").value = normalizeDirection(trade.direction);
  $("f_buyDate").value = trade.buy_date || "";
  $("f_buyPrice").value = formatInputNumber(trade.buy_price);
  state.captureInitialStop = getTradeInitialStop(trade);
  state.captureSuggestedStop = getTradeSuggestedStop(trade);
  $("f_initialStopLoss").value = formatInputNumber(state.captureInitialStop);
  $("f_stopLoss").value = formatInputNumber(trade.stop_loss);
  $("f_suggestedStopLoss").value = formatInputNumber(state.captureSuggestedStop);
  $("f_shares").value = formatInputNumber(trade.shares);
  $("f_stopReason").value = trade.stop_reason || "";
  $("f_targetPct").value = formatInputNumber(getTradeTargetPct(trade));
  $("f_targetPrice").value = formatInputNumber(getTradeTargetPrice(trade), 3);
  $("f_buyComm").value = formatInputNumber(getCommissionValue(trade.buy_comm, 1));
  $("f_chanHigh").value = formatInputNumber(trade.chan_high);
  $("f_chanLow").value = formatInputNumber(trade.chan_low);
  $("f_dayHigh").value = formatInputNumber(trade.day_high);
  $("f_dayLow").value = formatInputNumber(trade.day_low);
  $("f_sellDate").value = trade.sell_date || "";
  $("f_sellPrice").value = formatInputNumber(trade.sell_price);
  $("f_sellComm").value = formatInputNumber(getCommissionValue(trade.sell_comm, isTradeClosed(trade) ? 1 : 0));
  $("f_sellHigh").value = formatInputNumber(trade.sell_high);
  $("f_sellLow").value = formatInputNumber(trade.sell_low);
  $("f_sellReason").value = trade.sell_reason || "";
  $("f_review").value = trade.review || "";
  updateCaptureHeader();
  computeCapture();
  setSection("capture");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function getNumberInputValue(id) {
  return parseNumberValue($(id)?.value);
}

function computeCapture() {
  const direction = normalizeDirection($("f_direction").value);
  const buyPrice = getNumberInputValue("f_buyPrice");
  const stopLoss = getNumberInputValue("f_stopLoss");
  const initialStop = state.captureInitialStop ?? stopLoss;
  const suggestedStop = state.captureSuggestedStop;
  const shares = getNumberInputValue("f_shares");
  const sellPrice = getNumberInputValue("f_sellPrice");
  const targetPct = getNumberInputValue("f_targetPct");
  const buyComm = getCommissionValue($("f_buyComm").value, 1);
  const sellComm = getCommissionValue($("f_sellComm").value, hasTextValue($("f_sellDate").value) ? 1 : 0);
  const targetPrice = getTargetPrice(buyPrice, targetPct, direction);
  if (targetPrice !== null) $("f_targetPrice").value = targetPrice.toFixed(3);
  else $("f_targetPrice").value = "";

  const currentRisk = getRiskNumbers();
  const previewRisk = getRiskNumbers(true);
  const { singleStop } = currentRisk;
  const maxLoss = getSettings().total ? singleStop : null;
  const riskPerShare = getRiskPerShare(buyPrice, stopLoss, direction);
  const usedStop =
    buyPrice !== null && stopLoss !== null && shares !== null
      ? getTradeUsedStop({
          buy_price: buyPrice,
          stop_loss: stopLoss,
          shares,
          direction,
          sell_price: sellPrice,
          sell_date: $("f_sellDate").value || null,
        })
      : null;
  const recommendedShares = getRecommendedShares(maxLoss, buyPrice, stopLoss, direction);
  const stopStatus = getStopStatus(buyPrice, stopLoss, direction);
  const grossPnl = calculateGrossPnl(buyPrice, sellPrice, shares, direction);
  const netPnl = calculateNetPnl(grossPnl, buyComm, sellComm);

  $("calcMaxLoss").textContent = maxLoss === null ? "先设置总资金" : formatCurrency(maxLoss, 2);
  $("f_initialStopLoss").value = formatInputNumber(initialStop, 3);
  $("f_suggestedStopLoss").value = formatInputNumber(suggestedStop, 3);
  $("calcInitialStopDisplay").textContent = initialStop === null ? "—" : formatCurrency(initialStop, 3);
  $("calcCurrentStopDisplay").textContent = stopLoss === null ? "—" : formatCurrency(stopLoss, 3);
  $("calcSuggestedStopDisplay").textContent = suggestedStop === null ? "—" : formatCurrency(suggestedStop, 3);
  $("calcRiskPerShare").textContent = riskPerShare === null ? "—" : formatCurrency(riskPerShare, 3);
  $("calcUsedStop").textContent = usedStop === null ? "—" : formatCurrency(usedStop, 2);
  $("calcRecommendedShares").textContent = recommendedShares === null ? "—" : formatNumber(recommendedShares, 0);
  $("calcTargetPrice").textContent = targetPrice === null ? "—" : formatCurrency(targetPrice, 3);
  $("calcLivePnl").textContent = netPnl === null ? "持仓中" : formatCurrency(netPnl, 2);
  $("calcLivePnl").className = netPnl === null ? "" : netPnl >= 0 ? "accent-safe" : "accent-danger";

  let stopText = "—";
  if (stopStatus) {
    if (stopStatus.type === "breakeven") stopText = "保本";
    if (stopStatus.type === "locked") stopText = `锁定 ${formatPercent(stopStatus.pct, 1)}`;
    if (stopStatus.type === "risk") stopText = `风险 ${formatPercent(stopStatus.pct, 1)}`;
  }
  $("calcStopState").textContent = stopText;
  $("calcExecutionHint").textContent = recommendedShares === null
    ? "先填写入场价、止损价和总资金"
    : `当前规则建议 ${formatNumber(recommendedShares, 0)} 股，方向为 ${getDirectionLabel(direction)}`;
  $("fillSharesBtn").disabled = recommendedShares === null;
  $("fillSharesInlineBtn").disabled = recommendedShares === null;

  if (previewRisk.monthBudget > 0 && previewRisk.remaining < 0 && getCapturePreviewTrade()) {
    showAlert(
      "captureAlert",
      `这笔交易会超出本月剩余止损额度 ${formatCurrency(Math.abs(previewRisk.remaining), 2)}，保存后剩余额度会变成 ${formatCurrency(previewRisk.remaining, 2)}。`,
      "danger"
    );
  } else {
    showAlert("captureAlert", "");
  }

  renderSummary(true);
}

function applyRecommendedShares() {
  const { singleStop } = getRiskNumbers();
  const recommendedShares = getRecommendedShares(
    singleStop,
    getNumberInputValue("f_buyPrice"),
    getNumberInputValue("f_stopLoss"),
    $("f_direction").value
  );
  if (recommendedShares === null) return;
  $("f_shares").value = String(recommendedShares);
  computeCapture();
}

function insertReviewTemplate(kind) {
  const textarea = $("f_review");
  if (!textarea) return;
  const template = REVIEW_TEMPLATES[kind];
  textarea.value = textarea.value.trim() ? `${textarea.value.trim()}\n\n${template}` : template;
}

function getCapturePayload() {
  const stock = $("f_stock").value.trim().toUpperCase();
  const direction = normalizeDirection($("f_direction").value);
  const buyPrice = getNumberInputValue("f_buyPrice");
  const stopLoss = getNumberInputValue("f_stopLoss");
  const shares = getNumberInputValue("f_shares");
  const sellPrice = getNumberInputValue("f_sellPrice");
  const sellDate = $("f_sellDate").value || null;
  const buyComm = getCommissionValue($("f_buyComm").value, 1);
  const sellComm = getCommissionValue($("f_sellComm").value, sellDate ? 1 : 0);
  const targetPct = getNumberInputValue("f_targetPct");
  const targetPrice = getTargetPrice(buyPrice, targetPct, direction);
  const grossPnl = calculateGrossPnl(buyPrice, sellPrice, shares, direction);
  const netPnl = calculateNetPnl(grossPnl, buyComm, sellComm);
  const usedStop =
    buyPrice !== null && stopLoss !== null && shares !== null
      ? getTradeUsedStop({
          buy_price: buyPrice,
          stop_loss: stopLoss,
          shares,
          direction,
          sell_price: sellPrice,
          sell_date: sellDate,
        })
      : null;

  return {
    stock,
    direction,
    buy_price: buyPrice,
    shares,
    initial_stop_loss: state.captureInitialStop ?? stopLoss,
    stop_loss: stopLoss,
    stop_reason: $("f_stopReason").value.trim() || null,
    buy_date: $("f_buyDate").value || null,
    day_high: getNumberInputValue("f_dayHigh"),
    day_low: getNumberInputValue("f_dayLow"),
    target_price: targetPrice,
    target_pct: targetPct,
    chan_high: getNumberInputValue("f_chanHigh"),
    chan_low: getNumberInputValue("f_chanLow"),
    sell_price: sellPrice,
    sell_date: sellDate,
    sell_high: getNumberInputValue("f_sellHigh"),
    sell_low: getNumberInputValue("f_sellLow"),
    sell_reason: $("f_sellReason").value.trim() || null,
    buy_comm: buyComm,
    sell_comm: sellComm,
    review: $("f_review").value.trim() || null,
    used_stop: usedStop,
    pnl: grossPnl,
    pnl_net: netPnl,
  };
}

function validateCapturePayload(payload) {
  if (!payload.stock) return "请填写股票代码";
  if (payload.buy_price === null) return "请填写入场价";
  if (payload.stop_loss === null) return "请填写止损价";
  if (payload.shares === null) return "请填写股数";
  if (getRiskPerShare(payload.buy_price, payload.stop_loss, payload.direction) === 0) return "止损价需要位于风险有效的一侧";
  if (hasPartialSellInfo(payload.sell_price, payload.sell_date)) return payload.sell_price === null ? "填写平仓日期时，也请填写平仓价" : "填写平仓价时，也请填写平仓日期";
  return "";
}

async function saveTrade() {
  const payload = getCapturePayload();
  const error = validateCapturePayload(payload);
  if (error) {
    showAlert("captureAlert", error, "danger");
    return;
  }

  const previewRisk = getRiskNumbers(true);
  if (previewRisk.monthBudget > 0 && previewRisk.remaining < 0) {
    const confirmed = window.confirm(
      `这笔交易会超出本月剩余止损额度 ${formatCurrency(Math.abs(previewRisk.remaining), 2)}，保存后剩余额度会变成 ${formatCurrency(previewRisk.remaining, 2)}。仍然继续保存吗？`
    );
    if (!confirmed) return;
  }

  const button = $("captureSaveBtn");
  button.disabled = true;
  button.textContent = state.editingId ? "保存中…" : "创建中…";

  try {
    if (state.editingId) {
      await apiRequest(`/trades/${encodeURIComponent(state.editingId)}`, { method: "PUT", body: payload });
      showGlobalAlert("交易已更新", "success");
    } else {
      await apiRequest("/trades", { method: "POST", body: payload });
      showGlobalAlert("交易已保存", "success");
    }
    await loadTrades();
    clearCaptureForm();
    refreshAll();
    setSection("ledger");
  } catch (error) {
    showAlert("captureAlert", error.message || String(error), "danger");
  } finally {
    button.disabled = false;
    button.textContent = state.editingId ? "保存修改" : "保存交易";
    updateCaptureHeader();
  }
}

async function deleteTrade(id) {
  const trade = state.trades.find((item) => String(item.id) === String(id));
  if (!trade) return;
  if (!window.confirm(`确认删除 ${trade.stock} 这笔交易吗？`)) return;

  try {
    await apiRequest(`/trades/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadTrades();
    if (String(state.editingId) === String(id)) clearCaptureForm();
    refreshAll();
    showGlobalAlert("交易已删除", "success");
  } catch (error) {
    showGlobalAlert(error.message || String(error), "danger");
  }
}

async function loadTrades() {
  state.trades = await apiRequest("/trades");
}

async function loadSettings() {
  try {
    const row = await apiRequest("/settings");
    state.settings = settingsRowToState(row);
    cacheSettings();
  } catch (_) {
    state.settings = normalizeSettings(readStoredJson("tradeSettings", {}));
  }
}

async function persistSettings() {
  await apiRequest("/settings", {
    method: "PUT",
    body: settingsStateToRow(getSettings()),
  });
  showAlert("settingsAlert", "设置已同步到本地 SQLite", "success");
}

function scheduleSettingsSave() {
  const total = parseNumberValue($("s_total")?.value);
  const singleStop = parseNumberValue($("s_singleStop")?.value);
  const monthStop = parseNumberValue($("s_monthStop")?.value);
  const month = $("s_month")?.value || getCurrentMonth();

  state.settings = normalizeSettings({
    total,
    singleStop,
    monthStop,
    month,
  });
  cacheSettings();
  syncMonthInputs();
  refreshAll();
  showAlert("settingsAlert", "设置已更新，正在同步本地数据库…", "warn");

  clearTimeout(state.settingsSaveTimer);
  state.settingsSaveTimer = setTimeout(async () => {
    try {
      await persistSettings();
    } catch (error) {
      showAlert("settingsAlert", error.message || String(error), "danger");
    }
  }, 450);
}

function renderSettings() {
  const settings = getSettings();
  $("s_total").value = settings.total ? formatInputNumber(settings.total) : "";
  $("s_singleStop").value = formatInputNumber(settings.singleStop, 1);
  $("s_monthStop").value = formatInputNumber(settings.monthStop, 1);
  $("s_month").value = settings.month;
}

function refreshAll() {
  syncMonthInputs();
  renderSummary();
  renderOverview();
  renderJournal();
  renderStats();
  renderSettings();
  computeCapture();
  wireDynamicButtons();
}

function wireDynamicButtons() {
  document.querySelectorAll("[data-edit-trade]").forEach((button) => {
    button.onclick = () => {
      const trade = state.trades.find((item) => String(item.id) === String(button.dataset.editTrade));
      if (trade) populateCaptureForm(trade);
    };
  });
  document.querySelectorAll("[data-delete-trade]").forEach((button) => {
    button.onclick = () => deleteTrade(button.dataset.deleteTrade);
  });
}

function exportData() {
  downloadJson(`triple-screen-journal-${getCurrentMonth()}.json`, {
    exported_at: new Date().toISOString(),
    settings: getSettings(),
    trades: state.trades,
  });
}

async function clearAll() {
  if (!window.confirm("确认清空全部交易数据吗？这个操作无法恢复。")) return;
  try {
    await apiRequest("/trades", { method: "DELETE" });
    await loadTrades();
    clearCaptureForm();
    refreshAll();
    showGlobalAlert("全部交易数据已清空", "success");
  } catch (error) {
    showGlobalAlert(error.message || String(error), "danger");
  }
}

async function bootApp() {
  syncShell("journal");
  setScreenState("boot", "检查本地 Journal API，并加载交易日志…");

  try {
    const health = await ensureApiReady();
    renderConnectionStatus(true, `本地 API 已连接 · ${health.server.host}:${health.server.port}`);
    setScreenState("app");
    await Promise.all([loadSettings(), loadTrades()]);
    syncMonthInputs();
    clearCaptureForm();
    refreshAll();
  } catch (error) {
    renderConnectionStatus(false, "本地 API 不可用");
    $("configError").textContent = error.message || String(error);
    setScreenState("config");
  }
}

function bindEvents() {
  $("retryConnectBtn").addEventListener("click", bootApp);
  $("newTradeBtn").addEventListener("click", () => {
    clearCaptureForm();
    setSection("capture");
  });
  $("goCaptureBtn").addEventListener("click", () => setSection("capture"));
  $("jumpCaptureBtn").addEventListener("click", () => setSection("capture"));

  document.querySelectorAll("[data-section-tab]").forEach((button) => {
    button.addEventListener("click", () => setSection(button.dataset.sectionTab));
  });

  MONTH_INPUT_IDS.forEach((id) => {
    $(id).addEventListener("change", (event) => setReportMonth(event.target.value));
  });
  $("s_month").addEventListener("change", (event) => setReportMonth(event.target.value));

  ["s_total", "s_singleStop", "s_monthStop"].forEach((id) => {
    $(id).addEventListener("input", scheduleSettingsSave);
  });

  $("journalStatusFilter").addEventListener("change", renderJournal);
  $("journalSearchInput").addEventListener("input", renderJournal);

  FORM_INPUT_IDS.forEach((id) => {
    const input = $(id);
    if (!input) return;
    input.addEventListener("input", computeCapture);
  });

  $("fillSharesBtn").addEventListener("click", applyRecommendedShares);
  $("fillSharesInlineBtn").addEventListener("click", applyRecommendedShares);
  $("captureSaveBtn").addEventListener("click", saveTrade);
  $("captureCancelBtn").addEventListener("click", clearCaptureForm);
  $("clearCaptureBtn").addEventListener("click", clearCaptureForm);
  $("insertPlanTemplateBtn").addEventListener("click", () => insertReviewTemplate("plan"));
  $("insertReviewTemplateBtn").addEventListener("click", () => insertReviewTemplate("review"));

  $("exportDataBtn").addEventListener("click", exportData);
  $("reloadDataBtn").addEventListener("click", async () => {
    await Promise.all([loadSettings(), loadTrades()]);
    refreshAll();
    showGlobalAlert("数据已从本地 SQLite 重新加载", "success");
  });
  $("clearAllBtn").addEventListener("click", clearAll);
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  bootApp();
});
