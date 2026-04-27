export const APP_CONFIG = {
  apiBase: "/api",
  settingsRowId: 1,
  fixedLocale: "en-US",
};

export function safeStorageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

export function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_) {}
}

export function readStoredJson(key, fallback) {
  const raw = safeStorageGet(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch (_) {
    return fallback;
  }
}

export function normalizeNumericText(value) {
  const text = String(value ?? "")
    .trim()
    .replace(/\s+/g, "")
    .replace(/，/g, ",")
    .replace(/。/g, ".");

  if (!text) return "";

  const lastComma = text.lastIndexOf(",");
  const lastDot = text.lastIndexOf(".");
  if (lastComma !== -1 && lastDot !== -1) {
    return lastComma > lastDot
      ? text.replace(/\./g, "").replace(/,/g, ".")
      : text.replace(/,/g, "");
  }
  if (lastComma !== -1) return text.replace(/,/g, ".");
  return text;
}

export function parseNumberValue(value) {
  if (value === null || value === undefined) return null;
  const normalized = normalizeNumericText(value);
  if (!normalized) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

export function hasNumberValue(value) {
  return parseNumberValue(value) !== null;
}

export function hasTextValue(value) {
  return String(value ?? "").trim() !== "";
}

export function formatNumber(value, decimals = 0) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  return new Intl.NumberFormat(APP_CONFIG.fixedLocale, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(num);
}

export function formatCurrency(value, decimals = 2) {
  const num = Number(value);
  return Number.isFinite(num) ? `$${formatNumber(num, decimals)}` : "—";
}

export function formatPercent(value, decimals = 1) {
  const num = Number(value);
  return Number.isFinite(num) ? `${formatNumber(num, decimals)}%` : "—";
}

export function formatInputNumber(value, decimals = null) {
  const num = parseNumberValue(value);
  if (num === null) return "";
  return decimals === null ? String(num) : num.toFixed(decimals);
}

export function formatShares(value) {
  const num = parseNumberValue(value);
  return num === null ? "—" : `${formatNumber(num, 0)} 股`;
}

export function formatDateLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

export function pad2(value) {
  return String(value).padStart(2, "0");
}

export function getLocalDateStamp(date = new Date()) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

export function getLocalMonthStamp(date = new Date()) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}`;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function normalizeDirection(value) {
  return String(value || "long").toLowerCase() === "short" ? "short" : "long";
}

export function normalizeSignalDirection(value) {
  return String(value || "LONG").toUpperCase() === "SHORT" ? "SHORT" : "LONG";
}

export function getDirectionLabel(value) {
  return normalizeDirection(value) === "short" ? "做空" : "做多";
}

export function getSignalDirectionLabel(value) {
  return normalizeSignalDirection(value) === "SHORT" ? "做空" : "做多";
}

export function getDirectionAccent(value) {
  return normalizeDirection(value) === "short" ? "accent-danger" : "accent-safe";
}

export function getCommissionValue(value, fallback = 0) {
  const parsed = parseNumberValue(value);
  return parsed === null ? fallback : Math.abs(parsed);
}

export function getTargetPrice(entryPrice, targetPct, direction = "long") {
  const entry = parseNumberValue(entryPrice);
  const pct = parseNumberValue(targetPct);
  if (entry === null || pct === null || pct < 0) return null;
  return normalizeDirection(direction) === "short"
    ? Math.max(0, entry * (1 - pct / 100))
    : entry * (1 + pct / 100);
}

export function getRiskPerShare(entryPrice, stopLoss, direction = "long") {
  const entry = parseNumberValue(entryPrice);
  const stop = parseNumberValue(stopLoss);
  if (entry === null || stop === null) return null;
  const diff = normalizeDirection(direction) === "short" ? stop - entry : entry - stop;
  return diff > 0 ? diff : 0;
}

export function getSignedStopBudgetPerShare(entryPrice, stopLoss, direction = "long") {
  const entry = parseNumberValue(entryPrice);
  const stop = parseNumberValue(stopLoss);
  if (entry === null || stop === null) return null;
  return normalizeDirection(direction) === "short" ? stop - entry : entry - stop;
}

export function getStopStatus(entryPrice, stopLoss, direction = "long") {
  const entry = parseNumberValue(entryPrice);
  const stop = parseNumberValue(stopLoss);
  if (entry === null || stop === null || entry <= 0) return null;
  const diff = normalizeDirection(direction) === "short" ? entry - stop : stop - entry;
  if (Math.abs(diff) < 1e-9) return { type: "breakeven", pct: 0 };
  if (diff > 0) return { type: "locked", pct: (diff / entry) * 100 };
  return { type: "risk", pct: (Math.abs(diff) / entry) * 100 };
}

export function getRecommendedShares(maxLoss, entryPrice, stopLoss, direction = "long") {
  const riskPerShare = getRiskPerShare(entryPrice, stopLoss, direction);
  const max = parseNumberValue(maxLoss);
  if (riskPerShare === null || riskPerShare <= 0 || max === null || max <= 0) return null;
  const shares = Math.floor(max / riskPerShare);
  return shares > 0 ? shares : 0;
}

export function hasCompleteSellInfo(sellPrice, sellDate) {
  return parseNumberValue(sellPrice) !== null && hasTextValue(sellDate);
}

export function hasPartialSellInfo(sellPrice, sellDate) {
  return (parseNumberValue(sellPrice) !== null) !== hasTextValue(sellDate);
}

export function isTradeClosed(trade) {
  return hasCompleteSellInfo(trade?.sell_price, trade?.sell_date);
}

export function calculateGrossPnl(entryPrice, exitPrice, shares, direction = "long") {
  const entry = parseNumberValue(entryPrice);
  const exit = parseNumberValue(exitPrice);
  const size = parseNumberValue(shares);
  if (entry === null || exit === null || size === null) return null;
  return normalizeDirection(direction) === "short" ? (entry - exit) * size : (exit - entry) * size;
}

export function calculateNetPnl(grossPnl, buyComm, sellComm) {
  const gross = parseNumberValue(grossPnl);
  if (gross === null) return null;
  return gross - getCommissionValue(buyComm, 0) - getCommissionValue(sellComm, 0);
}

export function getTradeNetPnl(trade) {
  if (!isTradeClosed(trade)) return null;
  const gross = calculateGrossPnl(trade.buy_price, trade.sell_price, trade.shares, trade.direction);
  return calculateNetPnl(gross, trade.buy_comm, trade.sell_comm);
}

export function getTradeTargetPct(trade) {
  const raw = parseNumberValue(trade?.target_pct);
  if (raw === null || raw < 0) return null;
  return raw > 0 && raw < 1 ? raw * 100 : raw;
}

export function getTradeTargetPrice(trade) {
  const stored = parseNumberValue(trade?.target_price);
  if (stored !== null) return stored;
  return getTargetPrice(trade?.buy_price, getTradeTargetPct(trade), trade?.direction);
}

export function getTradeUsedStop(trade) {
  if (isTradeClosed(trade)) return 0;
  const risk = getSignedStopBudgetPerShare(trade?.buy_price, trade?.stop_loss, trade?.direction);
  const shares = parseNumberValue(trade?.shares);
  if (risk !== null && shares !== null) return risk * shares;
  const stored = parseNumberValue(trade?.used_stop);
  return stored === null ? 0 : stored;
}

export function getTradeBuyMonth(trade) {
  return String(trade?.buy_date || trade?.created_at || "").slice(0, 7);
}

export function getTradeSellMonth(trade) {
  return isTradeClosed(trade) ? String(trade?.sell_date || "").slice(0, 7) : "";
}

export function isTradeRelevantToMonth(trade, month) {
  const buyMonth = getTradeBuyMonth(trade);
  const sellMonth = getTradeSellMonth(trade);
  if (!buyMonth) return false;
  return buyMonth <= month && (!sellMonth || sellMonth >= month);
}

export function isTradeOpenAtMonthEnd(trade, month) {
  const buyMonth = getTradeBuyMonth(trade);
  const sellMonth = getTradeSellMonth(trade);
  if (!buyMonth) return false;
  return buyMonth <= month && (!sellMonth || sellMonth > month);
}

export function parseTradeDateValue(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function getTradeAnchorDate(trade) {
  return parseTradeDateValue(trade?.buy_date) || parseTradeDateValue(trade?.created_at);
}

export function getTradeAgeInDays(trade, referenceDate = new Date()) {
  const anchor = getTradeAnchorDate(trade);
  if (!anchor) return null;
  return Math.max(0, Math.floor((referenceDate.getTime() - anchor.getTime()) / 86400000));
}

export function getTradeCompletionGaps(trade) {
  const gaps = [];
  if (!hasTextValue(trade?.stop_reason)) gaps.push("交易计划");
  if (!hasTextValue(trade?.review)) gaps.push("交易心得");
  if (!hasNumberValue(trade?.day_high) || !hasNumberValue(trade?.day_low)) gaps.push("买入日高低");
  if (!hasNumberValue(trade?.chan_high) || !hasNumberValue(trade?.chan_low)) gaps.push("通道高低点");
  if (isTradeClosed(trade)) {
    if (!hasTextValue(trade?.sell_reason)) gaps.push("平仓原因");
    if (!hasNumberValue(trade?.sell_high) || !hasNumberValue(trade?.sell_low)) gaps.push("平仓日高低");
  }
  return gaps;
}

export function getStatusMeta(trade) {
  const pnl = getTradeNetPnl(trade);
  if (hasPartialSellInfo(trade?.sell_price, trade?.sell_date)) {
    return { label: "待补平仓", tone: "warn" };
  }
  if (!isTradeClosed(trade)) {
    return { label: "持仓中", tone: "info" };
  }
  return pnl !== null && pnl >= 0
    ? { label: "盈利", tone: "safe" }
    : { label: "亏损", tone: "danger" };
}

export function normalizeSettings(source = {}) {
  return {
    total: parseNumberValue(source.total) ?? 0,
    singleStop: parseNumberValue(source.singleStop) ?? 2,
    monthStop: parseNumberValue(source.monthStop) ?? 6,
    month: String(source.month || getLocalMonthStamp()),
  };
}

export function settingsRowToState(row) {
  return normalizeSettings({
    total: row?.total,
    singleStop: row?.single_stop,
    monthStop: row?.month_stop,
    month: row?.report_month,
  });
}

export function settingsStateToRow(settings) {
  return {
    id: APP_CONFIG.settingsRowId,
    total: settings.total,
    single_stop: settings.singleStop,
    month_stop: settings.monthStop,
    report_month: settings.month,
  };
}

export async function apiRequest(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  const requestOptions = {
    method: options.method || "GET",
    headers,
  };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    requestOptions.body = JSON.stringify(options.body);
  }

  const response = await fetch(`${APP_CONFIG.apiBase}${path}`, requestOptions);
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_) {
      data = text;
    }
  }

  if (!response.ok) {
    throw new Error(data?.detail || data?.message || text || `HTTP ${response.status}`);
  }
  return data;
}

export function withTimeout(promise, ms, message) {
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      setTimeout(() => reject(new Error(message)), ms);
    }),
  ]);
}

export async function ensureApiReady(timeoutMs = 4000) {
  return withTimeout(apiRequest("/health"), timeoutMs, "连接本地 Journal API 超时");
}

export function setScreenState(view, bootMessage = "") {
  const boot = document.getElementById("bootScreen");
  const gate = document.getElementById("configGate");
  const app = document.getElementById("mainApp");
  const message = document.getElementById("bootMessage");
  if (message && bootMessage) message.textContent = bootMessage;
  if (boot) boot.style.display = view === "boot" ? "grid" : "none";
  if (gate) gate.style.display = view === "config" ? "grid" : "none";
  if (app) app.style.display = view === "app" ? "block" : "none";
}

export function syncShell(currentPage) {
  document.querySelectorAll("[data-app-link]").forEach((link) => {
    const isActive = link.getAttribute("data-app-link") === currentPage;
    link.classList.toggle("active", isActive);
  });

  const dateLabel = document.getElementById("shellDate");
  if (dateLabel) {
    dateLabel.textContent = new Date().toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "long",
      day: "numeric",
      weekday: "short",
    });
  }
}

export function renderConnectionStatus(healthy, detail = "") {
  const dot = document.getElementById("connectionDot");
  const label = document.getElementById("connectionLabel");
  if (dot) dot.className = `status-dot ${healthy ? "healthy" : "error"}`;
  if (label) label.textContent = healthy ? (detail || "本地 API 已连接") : (detail || "本地 API 不可用");
}

export function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
