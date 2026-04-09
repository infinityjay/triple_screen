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
  const tone = normalized === "TRIGGERED" ? "safe" : normalized === "WATCHLIST" ? "info" : "warn";
  const label = normalized === "TRIGGERED" ? "已触发" : normalized === "WATCHLIST" ? "观察中" : normalized || "未知";
  return `<span class="badge badge-${tone}">${escapeHtml(label)}</span>`;
}

function buildExecutionPlan(item) {
  const hourly = item.hourly || {};
  const exits = item.exits || {};
  if (hourly.entry_price !== undefined) {
    return [
      `触发价 ${formatCurrency(hourly.entry_price, 3)}`,
      exits.stop_loss !== undefined ? `止损 ${formatCurrency(exits.stop_loss, 3)}` : "",
      exits.take_profit !== undefined ? `目标 ${formatCurrency(exits.take_profit, 3)}` : "",
      exits.reward_risk_ratio !== undefined ? `RR ${formatNumber(exits.reward_risk_ratio, 2)}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  return normalizeSignalDirection(item.direction) === "SHORT"
    ? "等待下一根小时线确认下破，盘中沿上一根已收盘 K 线低点跟踪卖出止损。"
    : "等待下一根小时线确认上破，盘中沿上一根已收盘 K 线高点跟踪买入止损。";
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
      item.earnings?.reason,
      item.hourly?.status,
    ]
      .join(" ")
      .toLowerCase();

    if (statusFilter === "watchlist" && status !== "WATCHLIST") return false;
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
    ? `当前会话共 ${items.length} 个候选，其中 ${triggered.length} 个已经有小时线触发。`
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
      `当前有 ${pending.length} 个标的还在等待小时线触发。第二天盘中优先盯住它们的上一根已收盘 K 线高低点。`,
    ]);
  }
  if (triggered.length) {
    insights.push([
      "已有小时线确认",
      `当前有 ${triggered.length} 个标的已经触发，可以直接对照入场价、止损和 RR 评估执行优先级。`,
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
            <strong>${escapeHtml(buildExecutionPlan(item))}</strong>
            <p>${escapeHtml(item.summary || "—")}</p>
          </td>
          <td class="reason-cell">
            ${getReasonBlock("周线", weekly.trend_score, weekly.reason)}
          </td>
          <td class="reason-cell">
            ${getReasonBlock("日线", daily.setup_score, daily.reason)}
          </td>
          <td class="reason-cell">
            ${getReasonBlock("小时线", hourly.trigger_score, hourly.reason)}
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
          <td>
            <div class="stage-stack">
              <div class="stage-block">
                <strong>入场</strong>
                <p>${hourly.entry_price !== undefined ? formatCurrency(hourly.entry_price, 3) : "等待小时线确认"}</p>
              </div>
              <div class="stage-block">
                <strong>止损</strong>
                <p>${exits.stop_loss !== undefined ? formatCurrency(exits.stop_loss, 3) : "—"}</p>
              </div>
              <div class="stage-block">
                <strong>目标 / RR</strong>
                <p>${exits.take_profit !== undefined ? `${formatCurrency(exits.take_profit, 3)} · RR ${formatNumber(exits.reward_risk_ratio ?? 0, 2)}` : "—"}</p>
              </div>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  $("watchlistTableContainer").innerHTML = `
    <div class="list-table-wrap">
      <table>
        <thead>
          <tr>
            <th>标的</th>
            <th>状态 / 标签</th>
            <th>为什么在观察列表</th>
            <th>周线过滤</th>
            <th>日线 Setup</th>
            <th>小时线执行</th>
            <th>财报风险</th>
            <th>背离提醒</th>
            <th>执行参数</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

async function loadWatchlist(sessionDate = "") {
  const query = sessionDate ? `?session_date=${encodeURIComponent(sessionDate)}` : "";
  const payload = await apiRequest(`/watchlist${query}`);
  state.payload = payload;
  state.sessionDate = payload.session_date || "";
  renderSummary();
  renderSessions();
  renderInsights();
  renderTable();
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
  $("statusFilter").addEventListener("change", renderTable);
  $("directionFilter").addEventListener("change", renderTable);
  $("watchlistSearch").addEventListener("input", renderTable);
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  bootApp();
});
