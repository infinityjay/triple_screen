import {
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
  getRecommendedShares,
  getRiskPerShare,
  getStatusMeta,
  getStopStatus,
  getTargetPrice,
  getTradeAgeInDays,
  getTradeAnchorDate,
  getTradeCompletionGaps,
  getTradeNetPnl,
  getTradeSellMonth,
  getTradeTargetPct,
  getTradeTargetPrice,
  getTradeUsedStop,
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
    "Trade Plan",
    "1. Are weekly / daily / hourly structures aligned:",
    "2. Exact entry trigger:",
    "3. Initial stop and invalidation:",
    "4. Target / scale-out plan:",
    "5. Execution reminder for today:",
  ].join("\n"),
  review: [
    "Trade Review",
    "1. Best part of this trade:",
    "2. Biggest problem:",
    "3. Did I follow the plan:",
    "4. Did emotion interfere:",
    "5. What to do next time this setup appears:",
  ].join("\n"),
};

const SUGGESTED_STOP_METHOD_CODE = "TWO_BAR";
const TOTAL_STOP_BUDGET_PCT = 6;

const state = {
  trades: [],
  settings: normalizeSettings(readStoredJson("tradeSettings", {})),
  editingId: null,
  captureInitialStop: null,
  captureSuggestedStop: null,
  activeSection: "ledger",
  settingsSaveTimer: null,
  suggestedStopTimer: null,
  suggestedStopRequestSeq: 0,
  suggestedStopCache: {},
};

const MONTH_INPUT_IDS = ["journalMonthPicker", "statsMonthPicker"];
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

const _noop = new Proxy({}, { get: () => _noop, set: () => true });
function $(id) {
  return document.getElementById(id) ?? _noop;
}

function showAlert(containerId, message, tone = "warn") {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = message
    ? `<div class="alert ${tone}">${escapeHtml(message)}</div>`
    : "";
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
  const index = trades.findIndex(
    (trade) => String(trade.id) === String(preview.id),
  );
  if (index >= 0) trades[index] = preview;
  else trades.unshift(preview);
  return trades;
}

function getTradesForMonth(month, includeCapturePreview = false) {
  return getTradesSource(includeCapturePreview).filter((trade) =>
    isTradeRelevantToMonth(trade, month),
  );
}

function getClosedTradesForMonth(month, includeCapturePreview = false) {
  return getTradesForMonth(month, includeCapturePreview).filter(
    (trade) => getTradeSellMonth(trade) === month,
  );
}

function getOpenTradesForMonth(month, includeCapturePreview = false) {
  return getTradesForMonth(month, includeCapturePreview).filter((trade) =>
    isTradeOpenAtMonthEnd(trade, month),
  );
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

  if (
    !stock ||
    buyPrice === null ||
    stopLoss === null ||
    shares === null ||
    !buyDate
  ) {
    return null;
  }

  const existing = state.editingId
    ? state.trades.find(
        (trade) => String(trade.id) === String(state.editingId),
      ) || {}
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

function getMonthOpenRisk(month, includeCapturePreview = false) {
  const trades = getOpenTradesForMonth(month, includeCapturePreview);
  return trades.reduce((sum, trade) => sum + getTradeUsedStop(trade), 0);
}

function getMonthClosedNetPnl(month, includeCapturePreview = false) {
  return getClosedTradesForMonth(month, includeCapturePreview).reduce(
    (sum, trade) => sum + (getTradeNetPnl(trade) || 0),
    0,
  );
}

function getFilteredTrades() {
  const month = getCurrentMonth();
  const status = $("journalStatusFilter")?.value || "all";
  const query = ($("journalSearchInput")?.value || "").trim().toLowerCase();
  return getTradesForMonth(month)
    .filter((trade) => {
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
    })
    .sort((a, b) => {
      const aOpen = !isTradeClosed(a) ? 0 : 1;
      const bOpen = !isTradeClosed(b) ? 0 : 1;
      if (aOpen !== bOpen) return aOpen - bOpen;
      const aDate = getTradeAnchorDate(a)?.getTime() || 0;
      const bDate = getTradeAnchorDate(b)?.getTime() || 0;
      return bDate - aDate;
    });
}

function getOverdueIncompleteTrades(months = 3) {
  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setMonth(cutoff.getMonth() - months);

  return state.trades
    .filter((trade) => {
      const anchor = getTradeAnchorDate(trade);
      return (
        anchor && anchor <= cutoff && getTradeCompletionGaps(trade).length > 0
      );
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
  const completeCount = trades.filter(
    (trade) => getTradeCompletionGaps(trade).length === 0,
  ).length;
  return (completeCount / trades.length) * 100;
}

function getRiskNumbers(includeCapturePreview = false) {
  const settings = getSettings();
  const singleStop = settings.total * (settings.singleStop / 100);
  const monthBudget = settings.total * (TOTAL_STOP_BUDGET_PCT / 100);
  const openUsed = getMonthOpenRisk(settings.month, includeCapturePreview);
  const totalUsed = openUsed;
  const remaining = monthBudget - totalUsed;
  const pct = monthBudget > 0 ? (totalUsed / monthBudget) * 100 : 0;
  return {
    singleStop,
    monthBudget,
    totalStopPct: TOTAL_STOP_BUDGET_PCT,
    openUsed,
    totalUsed,
    remaining,
    pct,
  };
}

function truncateText(value, length = 100) {
  const text = String(value || "").trim();
  if (!text) return "—";
  return text.length > length ? `${text.slice(0, length).trim()}...` : text;
}

function getTradeInitialStop(trade) {
  return (
    parseNumberValue(trade?.initial_stop_loss) ??
    parseNumberValue(trade?.stop_loss)
  );
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
  const wins = closedTrades.filter(
    (trade) => (getTradeNetPnl(trade) || 0) >= 0,
  );
  const netClosed = getMonthClosedNetPnl(month, includeCapturePreview);
  const closedPct =
    settings.total > 0 ? (netClosed / settings.total) * 100 : null;
  const completionRate = getMonthlyCompletionRate(month);
  const overdue = getOverdueIncompleteTrades();
  const { singleStop, monthBudget, totalStopPct, openUsed, remaining, pct } =
    getRiskNumbers(includeCapturePreview);

  $("summaryTotal").textContent = settings.total
    ? formatCurrency(settings.total, 0)
    : "Not Set";
  $("summarySingleStop").textContent = settings.total
    ? formatCurrency(singleStop, 2)
    : "—";
  $("summaryMonthBudget").textContent = settings.total
    ? formatCurrency(monthBudget, 2)
    : "—";
  $("summaryRemaining").textContent = settings.total
    ? formatCurrency(remaining, 2)
    : "—";
  $("summaryClosedResult").textContent = closedTrades.length
    ? formatCurrency(netClosed, 2)
    : "—";
  $("summaryClosedResult").className =
    `summary-value ${netClosed >= 0 ? "accent-safe" : "accent-danger"}`;
  $("summaryClosedResultSub").textContent = closedTrades.length
    ? `${closedTrades.length} closed · ${wins.length} wins / ${closedTrades.length - wins.length} losses · of capital ${
        closedPct === null ? "—" : formatPercent(closedPct, 2)
      }`
    : "No closed trades this month";
  $("summaryOpenCount").textContent = String(openTrades.length);
  $("summaryUsedPct").textContent =
    closedPct === null ? "—" : formatPercent(closedPct, 2);
  $("summaryUsedText").textContent = closedTrades.length
    ? `Closed net P/L this month ${formatCurrency(netClosed, 2)} / Total Capital ${formatCurrency(settings.total, 0)}`
    : "No closed trades this month";
  $("summaryCompleteness").textContent =
    completionRate === null ? "No sample" : formatPercent(completionRate, 0);
  $("summaryCompletenessSub").textContent = overdue.length
    ? `${overdue.length} trades older than 3 months are still incomplete`
    : "This month and history are reasonably complete";
  $("summarySingleStopSub").textContent = settings.total
    ? `${formatPercent(settings.singleStop, 1)} rule enabled`
    : "Set total capital first";
  $("summaryMonthBudgetSub").textContent = settings.total
    ? `${formatPercent(totalStopPct, 1)} total stop budget, current positions only`
    : "Total stop budget is not set";
  $("summaryRemainingSub").textContent =
    `Current position usage ${formatCurrency(openUsed, 2)} · remaining ${formatCurrency(remaining, 2)}`;
  $("summaryOpenCountSub").textContent = openTrades.length
    ? `including ${openTrades.filter((trade) => normalizeDirection(trade.direction) === "short").length} shorts`
    : "No month-end positions";
  $("summaryRiskFill").style.width =
    `${Math.max(0, Math.min(100, Math.abs(closedPct ?? 0)))}%`;

  $("monthHeadline").textContent = `Current Month: ${month}`;
  $("monthHeadlineBody").textContent =
    `${closedTrades.length} closed, Net Result ${closedTrades.length ? formatCurrency(netClosed, 2) : "—"}, ${openTrades.length} open positions currently use stop budget ${formatCurrency(openUsed, 2)}.`;
  $("journalMonthCurrent").textContent = `Current Month: ${month}`;
  $("statsMonthCurrent").textContent = `Current Month: ${month}`;

  // compact risk bar in ledger header
  $("riskBarTotal").textContent = settings.total ? formatCurrency(settings.total, 0) : "Not Set";
  $("riskBarSingleStop").textContent = settings.total ? formatCurrency(singleStop, 2) : "—";
  $("riskBarBudget").textContent = settings.total ? formatCurrency(monthBudget, 2) : "—";
  const remainingEl = $("riskBarRemaining");
  remainingEl.textContent = settings.total ? formatCurrency(remaining, 2) : "—";
  if (settings.total) remainingEl.style.color = remaining <= 0 ? "var(--accent-danger, #c0392b)" : remaining < singleStop ? "var(--accent-warn, #d38a2e)" : "";
  $("riskBarOpen").textContent = String(openTrades.length);
  $("riskBarClosed").textContent = closedTrades.length ? formatCurrency(netClosed, 2) : "—";
  const closedEl = $("riskBarClosed");
  if (closedTrades.length) closedEl.style.color = netClosed >= 0 ? "var(--accent-safe, #27ae60)" : "var(--accent-danger, #c0392b)";

  const notes = [];
  if (!settings.total)
    notes.push(
      "Set total capital and risk percentages before the system can suggest position size.",
    );
  if (remaining <= 0)
    notes.push(
      "Current positions have used the stop budget; pause new entries and manage existing positions first.",
    );
  else if (pct >= 75)
    notes.push(
      "Current stop-budget usage is elevated; filter new entries more strictly.",
    );
  if (overdue.length)
    notes.push(
      `${overdue.length} old trades are incomplete, which will affect statistics.`,
    );
  if (!closedTrades.length)
    notes.push(
      "No closed trades this month; focus on execution quality and record completeness.",
    );
  $("heroNotes").innerHTML = notes.length
    ? notes
        .map((item) => `<div class="status-pill">${escapeHtml(item)}</div>`)
        .join("")
    : `<div class="status-pill">This month is complete; keep maintaining the journal at the same rhythm.</div>`;
}

function renderOverview() {
  const overdue = getOverdueIncompleteTrades();
  const openTrades = getOpenTradesForMonth(getCurrentMonth());
  const recentTrades = [...state.trades]
    .slice()
    .sort(
      (a, b) =>
        (getTradeAnchorDate(b)?.getTime() || 0) -
        (getTradeAnchorDate(a)?.getTime() || 0),
    )
    .slice(0, 5);

  $("reminderSummary").innerHTML = overdue.length
    ? `There are <strong>${overdue.length}</strong> trades, fill in trade plan, review, and price ranges first.`
    : "No trades older than 3 months remain incomplete.";

  $("reminderList").innerHTML = overdue.length
    ? overdue
        .slice(0, 6)
        .map((trade) => {
          const gaps = getTradeCompletionGaps(trade);
          return `
            <div class="reminder-item">
              <strong>${escapeHtml(trade.stock || "—")} · ${escapeHtml(getDirectionLabel(trade.direction))}</strong>
              <p>${formatDateLabel(trade.buy_date || trade.created_at)} opened, ${getTradeAgeInDays(trade) || 0} days old; missing: ${escapeHtml(gaps.join(", "))}</p>
              <div class="btn-row" style="margin-top:12px">
                <button class="btn btn-secondary" type="button" data-edit-trade="${escapeHtml(String(trade.id))}">Continue Editing</button>
              </div>
            </div>
          `;
        })
        .join("")
    : `<div class="empty-state">No long-running incomplete trades.</div>`;

  const focusItems = openTrades.length ? openTrades.slice(0, 5) : recentTrades;
  $("focusList").innerHTML = focusItems.length
    ? focusItems
        .map((trade) => {
          const pnl = getTradeNetPnl(trade);
          const meta = getStatusMeta(trade);
          return `
            <div class="focus-item">
              <strong>${escapeHtml(trade.stock || "—")} · ${escapeHtml(getDirectionLabel(trade.direction))}</strong>
              <p>${escapeHtml(meta.label)} · Entry ${formatCurrency(trade.buy_price, 3)} · Shares ${formatShares(trade.shares)} · ${pnl === null ? "Waiting for close result" : `Net Result ${formatCurrency(pnl, 2)}`}</p>
              <div class="btn-row" style="margin-top:12px">
                <button class="btn btn-secondary" type="button" data-edit-trade="${escapeHtml(String(trade.id))}">Edit</button>
              </div>
            </div>
          `;
        })
        .join("")
    : `<div class="empty-state">No trades yet; enter the first one.</div>`;

  const month = getCurrentMonth();
  const closedTrades = getClosedTradesForMonth(month);
  const insights = [];
  const { pct, remaining, openUsed } = getRiskNumbers();
  if (remaining <= 0)
    insights.push([
      "Current Stop Budget Is Maxed",
      "Open risk has consumed the total stop budget; pause new positions.",
    ]);
  else if (pct >= 75)
    insights.push([
      "Current Stop Usage Is Elevated",
      `Position usage ${formatCurrency(openUsed, 2)}, Reduce size for new positions.`,
    ]);
  if (openTrades.length)
    insights.push([
      "Prioritize Open Positions",
      `There are ${openTrades.length} open, keep stops and tracking records updated first.`,
    ]);
  if (!closedTrades.length)
    insights.push([
      "Small Monthly Sample",
      "Do not over-interpret win rate and net result when there are too few closed trades.",
    ]);
  if (!insights.length)
    insights.push([
      "Process On Track",
      "Risk, sample size, and record completeness are all acceptable.",
    ]);
  $("monthInsights").innerHTML = insights
    .map(
      ([title, body]) =>
        `<div class="insight-item"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(body)}</p></div>`,
    )
    .join("");
}

function renderJournalRail(list) {
  if (!list.length) {
    $("journalRailContainer").innerHTML =
      `<div class="empty-state">No trade cards match the current filters.</div>`;
    return;
  }

  const cards = list
    .map((trade) => {
      const meta = getStatusMeta(trade);
      const pnl = getTradeNetPnl(trade);
      const gaps = getTradeCompletionGaps(trade);
      const target = getTradeTargetPrice(trade);
      const usedStop = getTradeUsedStop(trade);
      const isClosed = isTradeClosed(trade);
      const initialStop = getTradeInitialStop(trade);
      const currentStop = getTradeCurrentStop(trade);
      const primaryNote =
        trade.stop_reason ||
        trade.sell_reason ||
        trade.review ||
        "No notes have been added for this trade yet.";

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
              <span>Entry</span>
              <strong>${formatCurrency(trade.buy_price, 3)}</strong>
            </div>
            <div>
              <span>${isClosed ? "Closed Result" : "Risk Usage"}</span>
              <strong class="${pnl === null ? "" : pnl >= 0 ? "tone-safe" : "tone-danger"}">${isClosed ? formatCurrency(pnl, 2) : formatCurrency(usedStop, 2)}</strong>
            </div>
            <div>
              <span>Target</span>
              <strong>${formatCurrency(target, 3)}</strong>
            </div>
            <div>
              <span>Current Protective Stop</span>
              <strong>${formatCurrency(currentStop, 3)}</strong>
            </div>
            <div>
              <span>Result</span>
              <strong class="${pnl === null ? "" : pnl >= 0 ? "tone-safe" : "tone-danger"}">${pnl === null ? "Open" : formatCurrency(pnl, 2)}</strong>
            </div>
          </div>
          <div class="journal-rail-body">
            <div class="journal-rail-block">
              <strong>Plan / Reason</strong>
              <p>${escapeHtml(truncateText(primaryNote, 120))}</p>
            </div>
            <div class="journal-rail-footer">
              <span>${gaps.length ? `Missing ${escapeHtml(gaps.join(", "))}` : "Record complete"}</span>
              <span>${formatShares(trade.shares)} · Initial Stop ${formatCurrency(initialStop, 3)} · Current Protective Stop ${formatCurrency(currentStop, 3)}</span>
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
    $("journalTableContainer").innerHTML =
      `<div class="empty-state">No trade records match the current filters.</div>`;
    return;
  }

  const rows = list
    .map((trade) => {
      const meta = getStatusMeta(trade);
      const pnl = getTradeNetPnl(trade);
      const gaps = getTradeCompletionGaps(trade);
      const initialStop = getTradeInitialStop(trade);
      const currentStop = getTradeCurrentStop(trade);
      const atr1xStop = parseNumberValue(trade?.suggested_stop_candidate);
      const atr2xStop = parseNumberValue(trade?.suggested_stop_atr_2x);
      const hourlyStop = parseNumberValue(
        trade?.suggested_stop_hourly_safezone,
      );
      const nickStop = parseNumberValue(trade?.suggested_stop_nick);
      const riskOrResult = isTradeClosed(trade)
        ? pnl === null
          ? "—"
          : formatCurrency(pnl, 2)
        : formatCurrency(getTradeUsedStop(trade), 2);
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
          <td class="reason-cell">
            ${atr1xStop !== null && atr1xStop !== undefined ? `<div><span class="stop-label">ATR 1x</span> ${formatCurrency(atr1xStop, 3)}</div>` : ""}
            ${atr2xStop !== null && atr2xStop !== undefined ? `<div><span class="stop-label">ATR 2x</span> ${formatCurrency(atr2xStop, 3)}</div>` : ""}
            ${hourlyStop !== null && hourlyStop !== undefined ? `<div><span class="stop-label">Hourly SZ</span> ${formatCurrency(hourlyStop, 3)}</div>` : ""}
            ${nickStop !== null && nickStop !== undefined ? `<div><span class="stop-label">Nick</span> ${formatCurrency(nickStop, 3)}</div>` : ""}
            ${atr1xStop === null && atr2xStop === null && hourlyStop === null && nickStop === null ? "—" : ""}
          </td>
          <td class="${pnl === null ? "" : pnl >= 0 ? "tone-safe" : "tone-danger"}">${riskOrResult}</td>
          <td>${formatCurrency(getTradeTargetPrice(trade), 3)}</td>
          <td class="${pnl === null ? "" : pnl >= 0 ? "tone-safe" : "tone-danger"}">${pnl === null ? "Open" : formatCurrency(pnl, 2)}</td>
          <td class="reason-cell">${escapeHtml(trade.stop_reason || "—")}</td>
          <td class="reason-cell">${escapeHtml(trade.sell_reason || "—")}</td>
          <td class="reason-cell">${escapeHtml(gaps.length ? gaps.join(", ") : "Complete")}</td>
          <td class="reason-cell">${escapeHtml((trade.review || "").slice(0, 120) || "—")}</td>
          <td>
            <div class="inline-actions">
              <button class="btn btn-secondary" type="button" data-edit-trade="${escapeHtml(String(trade.id))}">Edit</button>
              <button class="btn btn-secondary" type="button" data-delete-trade="${escapeHtml(String(trade.id))}">Delete</button>
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
            <th>Symbol</th>
            <th>Status</th>
            <th>Entry Date</th>
            <th>Entry Price</th>
            <th>Shares</th>
            <th>Initial Stop Price</th>
            <th>Current Protective Stop</th>
            <th>EOD Updated Stops</th>
            <th>Risk / Result</th>
            <th>Target Price</th>
            <th>Net Result</th>
            <th>Trade Plan</th>
            <th>Exit Reason</th>
            <th>Missing Fields</th>
            <th>Review Summary</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderJournal() {
  const list = getFilteredTrades();
  $("journalCountLabel").textContent = `${list.length} trades`;
  renderJournalTable(list);
}

function upsertTradeInState(trade) {
  if (!trade || !trade.id) return;
  const index = state.trades.findIndex(
    (item) => String(item.id) === String(trade.id),
  );
  if (index >= 0) {
    state.trades[index] = trade;
    return;
  }
  state.trades.unshift(trade);
}

function renderStats() {
  const month = getCurrentMonth();
  const all = getTradesForMonth(month);
  const closed = getClosedTradesForMonth(month);
  const open = getOpenTradesForMonth(month);
  const net = getMonthClosedNetPnl(month);
  const wins = closed.filter((trade) => (getTradeNetPnl(trade) || 0) >= 0);
  const losses = closed.filter((trade) => (getTradeNetPnl(trade) || 0) < 0);
  const avgWin = wins.length
    ? wins.reduce((sum, trade) => sum + (getTradeNetPnl(trade) || 0), 0) /
      wins.length
    : null;
  const avgLoss = losses.length
    ? losses.reduce(
        (sum, trade) => sum + Math.abs(getTradeNetPnl(trade) || 0),
        0,
      ) / losses.length
    : null;
  const completionRate = getMonthlyCompletionRate(month);
  const risk = getRiskNumbers();
  const netPct =
    getSettings().total > 0 ? (net / getSettings().total) * 100 : null;

  let leadTitle = "Monthly Takeaway";
  let leadBody = "Keep record quality high; review risk before results.";
  if (!closed.length) {
    leadTitle = "Sample Too Small; Review Execution First";
    leadBody =
      "There are not enough closed trades this month; make sure plan, stops, and reviews are complete first.";
  } else if (risk.remaining <= 0) {
    leadTitle = "Current Stop Budget Is Maxed";
    leadBody =
      "Open risk has consumed the total stop budget; stop adding positions and manage existing positions first.";
  } else if (net < 0) {
    leadTitle = "Net Result Is Weak; Tighten Risk First";
    leadBody =
      "Closed trades are net negative this month; review execution issues in losing trades before expanding the sample.";
  } else if (risk.pct >= 75) {
    leadTitle = "Current Stop Usage Is Elevated";
    leadBody =
      "Open risk usage is high; keep new entries tight and focus on high-quality setups.";
  } else if ((completionRate || 0) < 70) {
    leadTitle = "Park Results; Complete Data First";
    leadBody =
      "Low record completeness directly hurts statistics; complete old trades first.";
  }

  $("statsLeadTitle").textContent = leadTitle;
  $("statsLeadBody").textContent = leadBody;
  $("statsLeadPills").innerHTML = [
    `<div class="stat-pill">Closed ${closed.length} trades</div>`,
    `<div class="stat-pill">Open ${open.length} trades</div>`,
    `<div class="stat-pill">Win Rate ${closed.length ? formatPercent((wins.length / closed.length) * 100, 0) : "—"}</div>`,
    `<div class="stat-pill">Completeness ${completionRate === null ? "—" : formatPercent(completionRate, 0)}</div>`,
  ].join("");

  $("statsGrid").innerHTML = [
    ["Net Result", closed.length ? formatCurrency(net, 2) : "—"],
    ["of capital", netPct === null ? "—" : formatPercent(netPct, 2)],
    [
      "Win Rate",
      closed.length
        ? formatPercent((wins.length / closed.length) * 100, 0)
        : "—",
    ],
    ["Average Win", avgWin === null ? "—" : formatCurrency(avgWin, 2)],
    ["Average Loss", avgLoss === null ? "—" : formatCurrency(avgLoss, 2)],
    ["Open Stop Usage", formatPercent(risk.pct, 0)],
    ["Current Open Stops", formatCurrency(risk.openUsed, 2)],
    ["Remaining Budget", formatCurrency(risk.remaining, 2)],
    ["Recorded Trades", String(all.length)],
    [
      "Complete Records",
      completionRate === null ? "—" : formatPercent(completionRate, 0),
    ],
  ]
    .map(
      ([label, value]) => `
        <div class="summary-card" style="padding:16px">
          <div class="summary-label">${escapeHtml(label)}</div>
          <div class="summary-value" style="font-size:26px">${escapeHtml(value)}</div>
        </div>
      `,
    )
    .join("");

  const suggestions = [];
  if (risk.remaining <= 0)
    suggestions.push([
      "Pause New Positions",
      "Current open stops have consumed the budget; handle existing positions first.",
    ]);
  if (losses.length > wins.length && closed.length >= 4)
    suggestions.push([
      "Review Loss Patterns",
      "Check whether mistakes cluster around the same setup type or execution step.",
    ]);
  if ((completionRate || 0) < 70)
    suggestions.push([
      "Complete Old Records",
      "Complete missing plans, exit reasons, and reviews before reading stats.",
    ]);
  if (!suggestions.length)
    suggestions.push([
      "Continue Current Process",
      "Keep maintaining the journal at the current rhythm and watch protective-stop updates.",
    ]);

  $("statsNarrative").innerHTML = suggestions
    .slice(0, 3)
    .map(
      ([title, body]) =>
        `<div class="insight-item"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(body)}</p></div>`,
    )
    .join("");
}

function updateCaptureHeader() {
  const editing = state.editingId !== null;
  $("captureTitle").textContent = editing ? "Edit Trade" : "New Trade";
  $("captureSubtitle").textContent = editing
    ? "You are editing an existing trade; saving updates SQLite directly."
    : "When adding a new trade, the system calculates risk and target levels live.";
  $("captureCancelBtn").classList.toggle("hidden", !editing);
  $("captureSaveBtn").textContent = editing ? "Save Changes" : "Save Trade";
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
  clearTimeout(state.suggestedStopTimer);
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
  $("f_suggestedStopLoss").value = formatInputNumber(
    state.captureSuggestedStop,
  );
  $("f_shares").value = formatInputNumber(trade.shares);
  $("f_stopReason").value = trade.stop_reason || "";
  $("f_targetPct").value = formatInputNumber(getTradeTargetPct(trade));
  $("f_targetPrice").value = formatInputNumber(getTradeTargetPrice(trade), 3);
  $("f_buyComm").value = formatInputNumber(
    getCommissionValue(trade.buy_comm, 1),
  );
  $("f_chanHigh").value = formatInputNumber(trade.chan_high);
  $("f_chanLow").value = formatInputNumber(trade.chan_low);
  $("f_dayHigh").value = formatInputNumber(trade.day_high);
  $("f_dayLow").value = formatInputNumber(trade.day_low);
  $("f_sellDate").value = trade.sell_date || "";
  $("f_sellPrice").value = formatInputNumber(trade.sell_price);
  $("f_sellComm").value = formatInputNumber(
    getCommissionValue(trade.sell_comm, isTradeClosed(trade) ? 1 : 0),
  );
  $("f_sellHigh").value = formatInputNumber(trade.sell_high);
  $("f_sellLow").value = formatInputNumber(trade.sell_low);
  $("f_sellReason").value = trade.sell_reason || "";
  $("f_review").value = trade.review || "";
  updateCaptureHeader();
  computeCapture();
  scheduleSuggestedStopLookup(true);
  setSection("capture");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function getNumberInputValue(id) {
  return parseNumberValue($(id)?.value);
}

function extractSuggestedStopFromAnalysis(payload) {
  const methods = payload?.system?.stop_methods?.methods || [];
  const target = methods.find(
    (method) => method?.code === SUGGESTED_STOP_METHOD_CODE,
  );
  if (!target) return null;
  return parseNumberValue(target.raw_price ?? target.price);
}

async function requestSuggestedStop(stock, direction) {
  const key = `${stock}:${direction}`;
  if (Object.prototype.hasOwnProperty.call(state.suggestedStopCache, key)) {
    state.captureSuggestedStop = state.suggestedStopCache[key];
    computeCapture();
    return;
  }

  const requestSeq = ++state.suggestedStopRequestSeq;
  try {
    const payload = await apiRequest("/technical-analysis", {
      method: "POST",
      body: { symbol: stock, include_ai: false },
    });
    if (requestSeq !== state.suggestedStopRequestSeq) return;
    const suggestedStop = extractSuggestedStopFromAnalysis(payload);
    state.suggestedStopCache[key] = suggestedStop;
    state.captureSuggestedStop = suggestedStop;
    computeCapture();
  } catch (_) {
    if (requestSeq !== state.suggestedStopRequestSeq) return;
    state.captureSuggestedStop = null;
    computeCapture();
  }
}

function scheduleSuggestedStopLookup(immediate = false) {
  clearTimeout(state.suggestedStopTimer);

  const stock = $("f_stock")?.value?.trim()?.toUpperCase() || "";
  const direction = normalizeDirection($("f_direction")?.value);
  if (!stock) {
    state.captureSuggestedStop = null;
    computeCapture();
    return;
  }

  const runLookup = () => requestSuggestedStop(stock, direction);
  if (immediate) {
    runLookup();
    return;
  }
  state.suggestedStopTimer = setTimeout(runLookup, 350);
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
  const sellComm = getCommissionValue(
    $("f_sellComm").value,
    hasTextValue($("f_sellDate").value) ? 1 : 0,
  );
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
  const recommendedShares = getRecommendedShares(
    maxLoss,
    buyPrice,
    stopLoss,
    direction,
  );
  const stopStatus = getStopStatus(buyPrice, stopLoss, direction);
  const grossPnl = calculateGrossPnl(buyPrice, sellPrice, shares, direction);
  const netPnl = calculateNetPnl(grossPnl, buyComm, sellComm);

  $("calcMaxLoss").textContent =
    maxLoss === null ? "Set total capital first" : formatCurrency(maxLoss, 2);
  $("f_initialStopLoss").value = formatInputNumber(initialStop, 3);
  $("f_suggestedStopLoss").value = formatInputNumber(suggestedStop, 3);
  $("calcInitialStopDisplay").textContent =
    initialStop === null ? "—" : formatCurrency(initialStop, 3);
  $("calcCurrentStopDisplay").textContent =
    stopLoss === null ? "—" : formatCurrency(stopLoss, 3);
  $("calcSuggestedStopDisplay").textContent =
    suggestedStop === null ? "—" : formatCurrency(suggestedStop, 3);
  $("calcRiskPerShare").textContent =
    riskPerShare === null ? "—" : formatCurrency(riskPerShare, 3);
  $("calcUsedStop").textContent =
    usedStop === null ? "—" : formatCurrency(usedStop, 2);
  $("calcRecommendedShares").textContent =
    recommendedShares === null ? "—" : formatNumber(recommendedShares, 0);
  $("calcTargetPrice").textContent =
    targetPrice === null ? "—" : formatCurrency(targetPrice, 3);
  $("calcLivePnl").textContent =
    netPnl === null ? "Open" : formatCurrency(netPnl, 2);
  $("calcLivePnl").className =
    netPnl === null ? "" : netPnl >= 0 ? "accent-safe" : "accent-danger";

  let stopText = "—";
  if (stopStatus) {
    if (stopStatus.type === "breakeven") stopText = "Breakeven";
    if (stopStatus.type === "locked")
      stopText = `Locked  ${formatPercent(stopStatus.pct, 1)}`;
    if (stopStatus.type === "risk")
      stopText = `Risk ${formatPercent(stopStatus.pct, 1)}`;
  }
  $("calcStopState").textContent = stopText;
  $("calcExecutionHint").textContent =
    recommendedShares === null
      ? stopStatus?.type === "breakeven" || stopStatus?.type === "locked"
        ? "Current protective stop is at breakeven/profit side; this open trade adds 0 risk usage"
        : "Enter entry price, stop price, and total capital first"
      : `Current rule suggests ${formatNumber(recommendedShares, 0)} shares, direction ${getDirectionLabel(direction)}`;
  $("fillSharesBtn").disabled = recommendedShares === null;
  $("fillSharesInlineBtn").disabled = recommendedShares === null;

  if (
    previewRisk.singleStop > 0 &&
    previewRisk.remaining < 0 &&
    getCapturePreviewTrade()
  ) {
    showAlert(
      "captureAlert",
      `This trade will exceed remaining open stop budget by ${formatCurrency(Math.abs(previewRisk.remaining), 2)}, remaining budget after save will become ${formatCurrency(previewRisk.remaining, 2)}.`,
      "warn",
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
    $("f_direction").value,
  );
  if (recommendedShares === null) return;
  $("f_shares").value = String(recommendedShares);
  computeCapture();
}

function insertReviewTemplate(kind) {
  const textarea = $("f_review");
  if (!textarea) return;
  const template = REVIEW_TEMPLATES[kind];
  textarea.value = textarea.value.trim()
    ? `${textarea.value.trim()}\n\n${template}`
    : template;
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
    protective_stop_basis: stopLoss === null ? null : "MANUAL",
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
  if (!payload.stock) return "Enter ticker";
  if (payload.buy_price === null) return "Enter entry price";
  if (payload.stop_loss === null) return "Enter stop price";
  if (payload.shares === null) return "Enter shares";
  if (
    getRiskPerShare(
      payload.buy_price,
      payload.initial_stop_loss,
      payload.direction,
    ) === 0
  )
    return "Initial stop price must be on the valid risk side";
  if (hasPartialSellInfo(payload.sell_price, payload.sell_date))
    return payload.sell_price === null
      ? "When entering exit date, also enter exit price"
      : "When entering exit price, also enter exit date";
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
  if (previewRisk.singleStop > 0 && previewRisk.remaining < 0) {
    showAlert(
      "captureAlert",
      `This trade will exceed total open stop budget by ${formatCurrency(Math.abs(previewRisk.remaining), 2)}, remaining budget after save will become ${formatCurrency(previewRisk.remaining, 2)}.`,
      "warn",
    );
  }

  const button = $("captureSaveBtn");
  button.disabled = true;
  button.textContent = state.editingId ? "Saving..." : "Creating...";

  try {
    let savedTrade = null;
    if (state.editingId) {
      savedTrade = await apiRequest(
        `/trades/${encodeURIComponent(state.editingId)}`,
        { method: "PUT", body: payload },
      );
      showGlobalAlert("Trade updated", "success");
    } else {
      savedTrade = await apiRequest("/trades", {
        method: "POST",
        body: payload,
      });
      showGlobalAlert("Trade saved", "success");
    }
    upsertTradeInState(savedTrade);
    clearCaptureForm();
    refreshAll();
    setSection("ledger");
  } catch (error) {
    showAlert("captureAlert", error.message || String(error), "danger");
  } finally {
    button.disabled = false;
    button.textContent = state.editingId ? "Save Changes" : "Save Trade";
    updateCaptureHeader();
  }
}

async function deleteTrade(id) {
  const trade = state.trades.find((item) => String(item.id) === String(id));
  if (!trade) return;
  if (!window.confirm(`Confirm deleting ${trade.stock} this trade?`)) return;

  try {
    await apiRequest(`/trades/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadTrades();
    if (String(state.editingId) === String(id)) clearCaptureForm();
    refreshAll();
    showGlobalAlert("Trade deleted", "success");
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
  showAlert("settingsAlert", "Settings synced to local SQLite", "success");
}

function scheduleSettingsSave() {
  const total = parseNumberValue($("s_total")?.value);
  const singleStop = parseNumberValue($("s_singleStop")?.value);
  const monthStop = TOTAL_STOP_BUDGET_PCT;
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
  showAlert(
    "settingsAlert",
    "Settings updated; syncing local database...",
    "warn",
  );

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
  $("s_monthStop").value = formatInputNumber(TOTAL_STOP_BUDGET_PCT, 1);
  $("s_month").value = settings.month;
}

function refreshAll() {
  syncMonthInputs();
  renderSummary();
  renderJournal();
  renderStats();
  renderSettings();
  computeCapture();
  wireDynamicButtons();
}

function wireDynamicButtons() {
  document.querySelectorAll("[data-edit-trade]").forEach((button) => {
    button.onclick = () => {
      const trade = state.trades.find(
        (item) => String(item.id) === String(button.dataset.editTrade),
      );
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
  if (!window.confirm("Clear all trade data? This action cannot be undone."))
    return;
  try {
    await apiRequest("/trades", { method: "DELETE" });
    await loadTrades();
    clearCaptureForm();
    refreshAll();
    showGlobalAlert("All trade data cleared", "success");
  } catch (error) {
    showGlobalAlert(error.message || String(error), "danger");
  }
}

async function bootApp() {
  syncShell("journal");
  setScreenState(
    "boot",
    "Checking local Journal API and loading Trade Journal...",
  );

  try {
    const health = await ensureApiReady();
    renderConnectionStatus(
      true,
      `Local API connected · ${health.server.host}:${health.server.port}`,
    );
    setScreenState("app");
    await Promise.all([loadSettings(), loadTrades()]);
    syncMonthInputs();
    clearCaptureForm();
    refreshAll();
  } catch (error) {
    renderConnectionStatus(false, "Local API unavailable");
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

  document.querySelectorAll("[data-section-tab]").forEach((button) => {
    button.addEventListener("click", () =>
      setSection(button.dataset.sectionTab),
    );
  });

  MONTH_INPUT_IDS.forEach((id) => {
    $(id).addEventListener("change", (event) =>
      setReportMonth(event.target.value),
    );
  });
  $("s_month").addEventListener("change", (event) =>
    setReportMonth(event.target.value),
  );

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
  $("f_stock").addEventListener("input", () => scheduleSuggestedStopLookup());
  $("f_direction").addEventListener("change", () =>
    scheduleSuggestedStopLookup(true),
  );

  $("fillSharesBtn").addEventListener("click", applyRecommendedShares);
  $("fillSharesInlineBtn").addEventListener("click", applyRecommendedShares);
  $("captureSaveBtn").addEventListener("click", saveTrade);
  $("captureCancelBtn").addEventListener("click", clearCaptureForm);
  $("clearCaptureBtn").addEventListener("click", clearCaptureForm);
  $("insertPlanTemplateBtn").addEventListener("click", () =>
    insertReviewTemplate("plan"),
  );
  $("insertReviewTemplateBtn").addEventListener("click", () =>
    insertReviewTemplate("review"),
  );

  $("exportDataBtn").addEventListener("click", exportData);
  $("reloadDataBtn").addEventListener("click", async () => {
    await Promise.all([loadSettings(), loadTrades()]);
    refreshAll();
    showGlobalAlert("Data reloaded from local SQLite", "success");
  });
  $("clearAllBtn").addEventListener("click", clearAll);
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  bootApp();
});
