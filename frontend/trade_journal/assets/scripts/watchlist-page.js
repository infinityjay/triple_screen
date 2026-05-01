import {
  apiRequest,
  ensureApiReady,
  escapeHtml,
  formatCurrency,
  formatDateLabel,
  formatNumber,
  getSignalDirectionLabel,
  normalizeSignalDirection,
  renderConnectionStatus,
  setScreenState,
  syncShell,
} from "./shared.js";

const state = {
  payload: null,
  sessionDate: "",
};

function $(id) {
  return document.getElementById(id);
}

function getReasonBlock(label, score, reason) {
  return `
    <div class="stage-block">
      <strong>${escapeHtml(label)}${score === null || score === undefined ? "" : ` · ${escapeHtml(formatNumber(score, 2))}`}</strong>
      <p>${escapeHtml(reason || "暂无说明")}</p>
    </div>
  `;
}

function getStatusBadge(status) {
  const normalized = String(status || "").toUpperCase();
  const tone = normalized === "TRIGGERED" ? "safe" : normalized === "WATCHLIST" ? "info" : normalized === "MONITOR" ? "warn" : "warn";
  const label =
    normalized === "TRIGGERED"
      ? "已触发"
      : normalized === "WATCHLIST"
        ? "待触发"
        : normalized === "MONITOR"
          ? "监测中"
          : normalized || "未知";
  return `<span class="badge badge-${tone}">${escapeHtml(label)}</span>`;
}

function truncateText(value, length = 92) {
  const text = String(value || "").trim();
  if (!text) return "—";
  return text.length > length ? `${text.slice(0, length).trim()}...` : text;
}

function setupHorizontalScrollbar(scrollContainerId, scrollbarId) {
  const scrollContainer = $(scrollContainerId);
  const scrollbar = $(scrollbarId);
  if (!scrollContainer || !scrollbar) return;

  const spacer = scrollbar.querySelector(".scrollbar-inner");
  if (!spacer) return;

  const syncSizes = () => {
    const contentWidth = scrollContainer.scrollWidth;
    const visibleWidth = scrollContainer.clientWidth;
    spacer.style.width = `${contentWidth}px`;
    scrollbar.style.display = contentWidth > visibleWidth + 4 ? "block" : "none";
  };

  let syncingFromContainer = false;
  let syncingFromBar = false;

  scrollContainer.onscroll = () => {
    if (syncingFromBar) return;
    syncingFromContainer = true;
    scrollbar.scrollLeft = scrollContainer.scrollLeft;
    syncingFromContainer = false;
  };

  scrollbar.onscroll = () => {
    if (syncingFromContainer) return;
    syncingFromBar = true;
    scrollContainer.scrollLeft = scrollbar.scrollLeft;
    syncingFromBar = false;
  };

  requestAnimationFrame(syncSizes);
  window.addEventListener("resize", syncSizes, { passive: true });
}

function buildExecutionPlan(item) {
  const hourly = item.hourly || {};
  const exits = item.exits || {};
  const entryPlan = hourly.entry_plan || item.daily?.entry_plan || {};
  const emaEntry = entryPlan.ema_penetration_entry;
  const breakoutEntry = entryPlan.breakout_entry;
  const target = exits.take_profit ?? exits.weekly_value_target?.target_price;
  if ((emaEntry !== undefined && emaEntry !== null) || (breakoutEntry !== undefined && breakoutEntry !== null)) {
    return [
      emaEntry !== undefined && emaEntry !== null ? `EMA穿透 ${formatCurrency(emaEntry, 3)}` : "",
      breakoutEntry !== undefined && breakoutEntry !== null ? `前日突破 ${formatCurrency(breakoutEntry, 3)}` : "",
      exits.initial_stop_nick !== undefined && exits.initial_stop_nick !== null ? `尼克 ${formatCurrency(exits.initial_stop_nick, 3)}` : "",
      target !== undefined && target !== null ? `目标 ${formatCurrency(target, 3)}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  return normalizeSignalDirection(item.direction) === "SHORT"
    ? "等待日线 Force 与动力系统到位，再用 EMA 上穿透价或前日低点下方一跳监测。"
    : "等待日线 Force 与动力系统到位，再用 EMA 下穿透价或前日高点上方一跳监测。";
}

function buildExecutionInline(item) {
  const hourly = item.hourly || {};
  const exits = item.exits || {};
  const entryPlan = hourly.entry_plan || item.daily?.entry_plan || {};
  const emaEntry = entryPlan.ema_penetration_entry !== undefined && entryPlan.ema_penetration_entry !== null ? formatCurrency(entryPlan.ema_penetration_entry, 3) : "—";
  const breakoutEntry = entryPlan.breakout_entry !== undefined && entryPlan.breakout_entry !== null ? formatCurrency(entryPlan.breakout_entry, 3) : "—";
  const stop = exits.initial_stop_nick !== undefined && exits.initial_stop_nick !== null
    ? `尼克 ${formatCurrency(exits.initial_stop_nick, 3)}`
    : exits.initial_stop_safezone !== undefined && exits.initial_stop_safezone !== null
      ? `SafeZone ${formatCurrency(exits.initial_stop_safezone, 3)}`
      : "待选择";
  const target = exits.take_profit !== undefined && exits.take_profit !== null
    ? formatCurrency(exits.take_profit, 3)
    : exits.weekly_value_target?.target_price !== undefined && exits.weekly_value_target?.target_price !== null
      ? formatCurrency(exits.weekly_value_target.target_price, 3)
      : "—";
  return `EMA穿透 ${emaEntry} | 前日突破 ${breakoutEntry} | ${stop} | 周线目标 ${target}`;
}

function getFilteredItems() {
  const items = state.payload?.items || [];
  const statusFilter = $("statusFilter").value;
  const directionFilter = $("directionFilter").value;
  const query = $("watchlistSearch").value.trim().toLowerCase();

  return items.filter((item) => {
    const status = String(item.opportunity_status || "").toUpperCase();
    const direction = normalizeSignalDirection(item.direction);
    const searchText = [
      item.symbol,
      item.summary,
      item.weekly?.reason,
      item.daily?.reason,
      item.hourly?.reason,
      item.daily?.force_index_ema2,
      item.daily?.impulse_color,
      item.weekly?.impulse_color,
      item.earnings?.reason,
      item.hourly?.status,
    ]
      .join(" ")
      .toLowerCase();

    if (statusFilter === "watchlist" && status !== "WATCHLIST") return false;
    if (statusFilter === "monitor" && status !== "MONITOR") return false;
    if (statusFilter === "triggered" && status !== "TRIGGERED") return false;
    if (directionFilter !== "all" && direction !== directionFilter) return false;
    if (query && !searchText.includes(query)) return false;
    return true;
  });
}

function renderSummary() {
  const items = state.payload?.items || [];
  const triggered = items.filter((item) => String(item.opportunity_status || "").toUpperCase() === "TRIGGERED");
  const longCount = items.filter((item) => normalizeSignalDirection(item.direction) === "LONG").length;
  const shortCount = items.length - longCount;
  const divergenceCount = items.filter((item) => item.strong_divergence).length;

  $("watchlistCount").textContent = String(items.length);
  $("watchlistTriggered").textContent = String(triggered.length);
  $("watchlistDirectionMix").textContent = `${longCount} / ${shortCount}`;
  $("watchlistDivergence").textContent = String(divergenceCount);
  $("sessionHeadline").textContent = `扫描会话：${state.payload?.session_date || "—"}`;
  $("sessionHeadlineBody").textContent = items.length
    ? `当前会话共 ${items.length} 个候选，其中 ${triggered.length} 个已经触及参考价。`
    : "当前会话没有合格候选。";
}

function renderSessions() {
  const sessions = state.payload?.available_sessions || [];
  $("sessionSelect").innerHTML = sessions.length
    ? sessions
        .map(
          (session) =>
            `<option value="${escapeHtml(session.session_date)}"${session.session_date === state.payload.session_date ? " selected" : ""}>${escapeHtml(session.session_date)} · ${escapeHtml(String(session.candidate_count))} 个</option>`
        )
        .join("")
    : `<option value="">暂无会话</option>`;

  $("sessionChips").innerHTML = sessions.length
    ? sessions
        .map((session) => {
          const active = session.session_date === state.payload.session_date ? " active" : "";
          return `
            <button class="session-chip${active}" type="button" data-session-date="${escapeHtml(session.session_date)}">
              ${escapeHtml(session.session_date)}
              <span class="mono"> ${escapeHtml(String(session.candidate_count))} 标的 / ${escapeHtml(String(session.triggered_count || 0))} 触发</span>
            </button>
          `;
        })
        .join("")
    : `<div class="empty-state">数据库里还没有观察列表快照。请先运行一次扫描。</div>`;

  document.querySelectorAll("[data-session-date]").forEach((button) => {
    button.addEventListener("click", () => loadWatchlist(button.dataset.sessionDate));
  });
}

function renderInsights() {
  const items = state.payload?.items || [];
  const triggered = items.filter((item) => String(item.opportunity_status || "").toUpperCase() === "TRIGGERED");
  const pending = items.filter((item) => String(item.opportunity_status || "").toUpperCase() !== "TRIGGERED");
  const insights = [];

  if (pending.length) {
    insights.push([
      "盘后候选默认先看周 / 日",
      `当前有 ${pending.length} 个标的还未触及参考价。第二天盘中优先盯 EMA 穿透价和前日高/低点外一跳。`,
    ]);
  }
  if (triggered.length) {
    insights.push([
      "已有参考价触发",
      `当前有 ${triggered.length} 个标的已经触及参考价，可以直接对照入场价、止损和周线目标评估执行优先级。`,
    ]);
  }
  if (items.some((item) => item.earnings?.warning || item.earnings?.blocked)) {
    insights.push([
      "注意财报窗口",
      "部分候选接近财报日，哪怕技术面成立，也要先确认是否仍符合你的事件风控规则。",
    ]);
  }
  if (items.some((item) => item.strong_divergence)) {
    insights.push([
      "强背离优先复核",
      "强背离不一定否定 setup，但说明趋势衰竭风险升高，执行上要更保守。",
    ]);
  }
  if (!insights.length) {
    insights.push(["暂无特殊提醒", "当前候选池相对干净，可以按正常节奏执行筛选。"]);
  }

  $("watchlistInsights").innerHTML = insights
    .map(([title, body]) => `<div class="insight-item"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(body)}</p></div>`)
    .join("");
}

function renderRail() {
  const items = getFilteredItems();
  $("watchlistRailCount").textContent = `${items.length} 张卡片`;

  if (!items.length) {
    $("watchlistRailContainer").innerHTML = `<div class="empty-state">当前筛选条件下没有可展示的观察卡片。</div>`;
    return;
  }

  const cards = items
    .map((item) => {
      const weekly = item.weekly || {};
      const daily = item.daily || {};
      const hourly = item.hourly || {};
      const earnings = item.earnings || {};
      const entryPlan = hourly.entry_plan || daily.entry_plan || {};
      const tags = [
        item.strong_divergence ? "强背离" : "",
        earnings.warning ? "财报临近" : "",
        String(item.opportunity_status || "").toUpperCase() === "TRIGGERED" ? "已触发" : "",
        String(item.opportunity_status || "").toUpperCase() === "MONITOR" ? "监测中" : "",
        ...(item.priority_tags || []),
      ].filter(Boolean);

      return `
        <article class="watchlist-rail-item">
          <div class="watchlist-rail-top">
            <div>
              <h3>${escapeHtml(item.symbol || "—")}</h3>
              <p>${escapeHtml(getSignalDirectionLabel(item.direction))} · 分数 ${escapeHtml(formatNumber(item.signal_score ?? item.candidate_score ?? 0, 2))}</p>
            </div>
            ${getStatusBadge(item.opportunity_status)}
          </div>
          <div class="watchlist-rail-tags">${tags.length ? tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("") : `<span class="tag">常规候选</span>`}</div>
          <div class="watchlist-rail-body">
            <div class="watchlist-rail-reason">
              <strong>入选理由</strong>
              <p>${escapeHtml(truncateText(item.summary || daily.reason || weekly.reason, 110))}</p>
            </div>
            <div class="watchlist-rail-split">
              <div>
                <span>周线动力</span>
                <strong>${escapeHtml(weekly.impulse_color || "—")} · ${escapeHtml(weekly.trend || "—")}</strong>
              </div>
              <div>
                <span>日线 Force</span>
                <strong>${escapeHtml(formatNumber(daily.force_index_ema2 ?? 0, 0))} · ${escapeHtml(daily.impulse_color || "—")}</strong>
              </div>
            </div>
            <div class="watchlist-rail-footer">
              <span>${escapeHtml(entryPlan.ema_penetration_entry !== undefined && entryPlan.ema_penetration_entry !== null ? `EMA穿透 ${formatCurrency(entryPlan.ema_penetration_entry, 3)}` : buildExecutionPlan(item))}</span>
              <span>${earnings.report_date ? `财报 ${escapeHtml(formatDateLabel(earnings.report_date))}` : "财报未知"}</span>
            </div>
          </div>
        </article>
      `;
    })
    .join("");

  $("watchlistRailContainer").innerHTML = `
    <div class="scrollbar-shell" id="watchlistRailScrollbar"><div class="scrollbar-inner"></div></div>
    <div class="watchlist-rail-shell" id="watchlistRailScroll">
      <div class="watchlist-rail">${cards}</div>
    </div>
  `;
  setupHorizontalScrollbar("watchlistRailScroll", "watchlistRailScrollbar");
}

function renderTable() {
  const items = getFilteredItems();
  if (!items.length) {
    $("watchlistTableContainer").innerHTML = `<div class="empty-state">当前筛选条件下没有观察列表项目。</div>`;
    return;
  }

  const rows = items
    .map((item) => {
      const weekly = item.weekly || {};
      const daily = item.daily || {};
      const hourly = item.hourly || {};
      const exits = item.exits || {};
      const earnings = item.earnings || {};
      const divergence = item.divergence || {};

      const tags = [
        item.strong_divergence ? "强背离" : "",
        earnings.warning ? "财报临近" : "",
        earnings.blocked ? "财报黑窗" : "",
        ...(item.priority_tags || []),
      ].filter(Boolean);

      return `
        <tr>
          <td class="symbol-cell">
            <strong>${escapeHtml(item.symbol || "—")}</strong>
            <span>${escapeHtml(getSignalDirectionLabel(item.direction))} · 分数 ${escapeHtml(formatNumber(item.signal_score ?? item.candidate_score ?? 0, 2))}</span>
          </td>
          <td>
            ${getStatusBadge(item.opportunity_status)}
            <div style="margin-top:8px">${tags.length ? tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join(" ") : `<span class="tag">常规候选</span>`}</div>
          </td>
          <td class="reason-cell">
            ${getReasonBlock(`周线动力 ${weekly.impulse_color || "—"}`, weekly.trend_score, weekly.reason)}
          </td>
          <td class="reason-cell">
            ${getReasonBlock(`日线 Force ${formatNumber(daily.force_index_ema2 ?? 0, 0)}`, daily.setup_score, daily.reason)}
          </td>
          <td class="reason-cell">
            ${getReasonBlock("触发价监测", hourly.trigger_score, hourly.reason || buildExecutionPlan(item))}
          </td>
          <td class="reason-cell">
            <strong>${earnings.report_date ? `财报 ${escapeHtml(formatDateLabel(earnings.report_date))}` : "财报未知"}</strong>
            <p>${escapeHtml(earnings.reason || "未提供财报信息")}</p>
          </td>
          <td class="reason-cell">
            <strong>周线：${divergence.weekly?.detected ? "有背离" : "无背离"} / 日线：${divergence.daily?.detected ? "有背离" : "无背离"}</strong>
            <p>${escapeHtml(
              divergence.daily?.reason ||
                divergence.weekly?.reason ||
                "暂无背离说明"
            )}</p>
          </td>
          <td class="execution-cell">
            <span class="execution-inline-text">${escapeHtml(buildExecutionInline(item))}</span>
          </td>
        </tr>
      `;
    })
    .join("");

  $("watchlistTableContainer").innerHTML = `
    <div class="scrollbar-shell" id="watchlistDetailScrollbar"><div class="scrollbar-inner"></div></div>
    <div class="list-table-wrap watchlist-detail-scroll" id="watchlistDetailScroll">
      <table class="watchlist-detail-table">
        <thead>
          <tr>
            <th>标的</th>
            <th>状态 / 标签</th>
            <th>周线过滤</th>
            <th>日线 Force</th>
            <th>触发价监测</th>
            <th>财报风险</th>
            <th>背离提醒</th>
            <th>执行参数</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  setupHorizontalScrollbar("watchlistDetailScroll", "watchlistDetailScrollbar");
}

function renderFilteredViews() {
  renderRail();
  renderTable();
}

async function loadWatchlist(sessionDate = "") {
  const query = sessionDate ? `?session_date=${encodeURIComponent(sessionDate)}` : "";
  const payload = await apiRequest(`/watchlist${query}`);
  state.payload = payload;
  state.sessionDate = payload.session_date || "";
  renderSummary();
  renderSessions();
  renderInsights();
  renderFilteredViews();
}

async function bootApp() {
  syncShell("watchlist");
  setScreenState("boot", "检查本地 Journal API，并加载观察列表…");
  try {
    const health = await ensureApiReady();
    renderConnectionStatus(true, `本地 API 已连接 · ${health.server.host}:${health.server.port}`);
    setScreenState("app");
    await loadWatchlist();
  } catch (error) {
    renderConnectionStatus(false, "本地 API 不可用");
    $("configError").textContent = error.message || String(error);
    setScreenState("config");
  }
}

function bindEvents() {
  $("retryConnectBtn").addEventListener("click", bootApp);
  $("refreshWatchlistBtn").addEventListener("click", () => loadWatchlist(state.sessionDate));
  $("sessionSelect").addEventListener("change", (event) => loadWatchlist(event.target.value));
  $("statusFilter").addEventListener("change", renderFilteredViews);
  $("directionFilter").addEventListener("change", renderFilteredViews);
  $("watchlistSearch").addEventListener("input", renderFilteredViews);
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  bootApp();
});
