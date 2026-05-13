import {
  apiRequest,
  ensureApiReady,
  escapeHtml,
  formatCurrency,
  formatDateLabel,
  formatNumber,
  normalizeSignalDirection,
  parseNumberValue,
  renderConnectionStatus,
  setScreenState,
  syncShell,
} from "./shared.js";

const state = {
  payload: null,
  sessionDate: "",
};

const _noop = new Proxy({}, { get: () => _noop, set: () => true });
function $(id) {
  return document.getElementById(id) ?? _noop;
}

function getReasonBlock(label, score, reason) {
  return `
    <div class="stage-block">
      <strong>${escapeHtml(label)}${score === null || score === undefined ? "" : ` · ${escapeHtml(formatNumber(score, 2))}`}</strong>
      <p>${escapeHtml(reason || "No notes yet")}</p>
    </div>
  `;
}

function getDirectionBadge(direction) {
  const isShort = normalizeSignalDirection(direction) === "SHORT";
  return `<span class="badge badge-direction ${isShort ? "badge-direction-short" : "badge-direction-long"}">${isShort ? "↓ Short" : "↑ Long"}</span>`;
}

function getStatusBadge(status) {
  const normalized = String(status || "").toUpperCase();
  const tone =
    normalized === "TRIGGERED"
      ? "safe"
      : normalized === "TOUCHED_ENTRY_PRICE"
        ? "warn"
        : normalized === "WATCHLIST"
          ? "info"
          : normalized === "MONITOR"
            ? "warn"
            : "warn";
  const label =
    normalized === "TRIGGERED"
      ? "Triggered"
      : normalized === "TOUCHED_ENTRY_PRICE"
        ? "Touched"
        : normalized === "WATCHLIST"
          ? "Waiting"
          : normalized === "MONITOR"
            ? "Monitor"
            : normalized || "Unknown";
  return `<span class="badge badge-${tone}">${escapeHtml(label)}</span>`;
}

function isPastDate(value) {
  if (!value) return false;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  const today = new Date();
  date.setHours(0, 0, 0, 0);
  today.setHours(0, 0, 0, 0);
  return date < today;
}

function getEarningsDisplay(earnings = {}) {
  if (isPastDate(earnings.report_date)) {
    return {
      label: "Earnings unknown",
      reason: `Cached earnings date ${formatDateLabel(earnings.report_date)} is stale. Waiting for an updated earnings date.`,
    };
  }
  return {
    label: earnings.report_date
      ? `Earnings ${formatDateLabel(earnings.report_date)}`
      : "Earnings unknown",
    reason: earnings.reason || "No earnings data provided",
  };
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
    scrollbar.style.display =
      contentWidth > visibleWidth + 4 ? "block" : "none";
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
  if (
    (emaEntry !== undefined && emaEntry !== null) ||
    (breakoutEntry !== undefined && breakoutEntry !== null)
  ) {
    return [
      emaEntry !== undefined && emaEntry !== null
        ? `EMA penetration ${formatCurrency(emaEntry, 3)}`
        : "",
      breakoutEntry !== undefined && breakoutEntry !== null
        ? `Previous-day break ${formatCurrency(breakoutEntry, 3)}`
        : "",
      ...([
        exits.initial_stop_hourly_safezone != null
          ? `Hourly SZ ${formatCurrency(exits.initial_stop_hourly_safezone, 3)}`
          : "",
        exits.initial_stop_safezone != null
          ? `SafeZone ${formatCurrency(exits.initial_stop_safezone, 3)}`
          : "",
        exits.initial_stop_nick != null
          ? `Nick ${formatCurrency(exits.initial_stop_nick, 3)}`
          : "",
      ]
        .filter(Boolean)
        .join(" / ") || ""),
      target !== undefined && target !== null
        ? `Target ${formatCurrency(target, 3)}`
        : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  return normalizeSignalDirection(item.direction) === "SHORT"
    ? "Wait for daily Force and impulse alignment, then monitor the EMA upside penetration or one tick below the previous-day low."
    : "Wait for daily Force and impulse alignment, then monitor the EMA downside penetration or one tick above the previous-day high.";
}

function getOrderPlan(item) {
  return item.order_plan || item.next_day_order_plan || {};
}

function buildOrderPlanInline(item) {
  const plan = getOrderPlan(item);
  const primary = plan.primary_order || {};
  const secondary = plan.secondary_order || {};
  const risk = plan.risk || {};
  if (!primary.stop_price && !secondary.limit_price)
    return buildExecutionInline(item);
  const action = String(primary.action || "").trim();
  const sideLabel = action
    ? action.charAt(0).toUpperCase() + action.slice(1).toLowerCase()
    : "Buy";
  const rsiState = String(item.daily?.rsi_state || "");
  const useEmaEntry = Boolean(item.daily?.entered_value_zone);
  const stopParts = [
    risk.initial_stop_hourly_safezone != null
      ? `Hourly SZ stop ${formatCurrency(risk.initial_stop_hourly_safezone, 2)}`
      : "",
    risk.initial_stop_safezone != null
      ? `SafeZone stop ${formatCurrency(risk.initial_stop_safezone, 2)}`
      : "",
    risk.initial_stop_nick != null
      ? `Nick stop ${formatCurrency(risk.initial_stop_nick, 2)}`
      : "",
  ]
    .filter(Boolean)
    .join("  ·  ");
  const orderText = useEmaEntry
    ? secondary.limit_price != null
      ? `EMA Entry: ${sideLabel} Limit — ${formatCurrency(secondary.limit_price, 2)}`
      : ""
    : primary.stop_price != null
      ? `Breakout Entry: ${sideLabel} Stop Limit — Stop ${formatCurrency(primary.stop_price, 2)}  Limit ${formatCurrency(primary.limit_price, 2)}`
      : "";
  return [orderText, stopParts || "—"].join("   ‖   ");
}

function getPlannedOrderFor(item) {
  const orders = state.payload?.planned_orders || [];
  const direction = normalizeSignalDirection(item.direction);
  return orders.find(
    (order) =>
      String(order.symbol || "").toUpperCase() ===
        String(item.symbol || "").toUpperCase() &&
      normalizeSignalDirection(order.direction) === direction,
  );
}

function buildExecutionInline(item) {
  const hourly = item.hourly || {};
  const exits = item.exits || {};
  const entryPlan = hourly.entry_plan || item.daily?.entry_plan || {};
  const emaEntry =
    entryPlan.ema_penetration_entry !== undefined &&
    entryPlan.ema_penetration_entry !== null
      ? formatCurrency(entryPlan.ema_penetration_entry, 3)
      : "—";
  const breakoutEntry =
    entryPlan.breakout_entry !== undefined && entryPlan.breakout_entry !== null
      ? formatCurrency(entryPlan.breakout_entry, 3)
      : "—";
  const stop =
    exits.initial_stop_hourly_safezone !== undefined &&
    exits.initial_stop_hourly_safezone !== null
      ? `Hourly SZ ${formatCurrency(exits.initial_stop_hourly_safezone, 3)}`
      : exits.initial_stop_nick !== undefined &&
          exits.initial_stop_nick !== null
        ? `Nick ${formatCurrency(exits.initial_stop_nick, 3)}`
        : exits.initial_stop_safezone !== undefined &&
            exits.initial_stop_safezone !== null
          ? `SafeZone ${formatCurrency(exits.initial_stop_safezone, 3)}`
          : "Choose manually";
  const target =
    exits.take_profit !== undefined && exits.take_profit !== null
      ? formatCurrency(exits.take_profit, 3)
      : exits.weekly_value_target?.target_price !== undefined &&
          exits.weekly_value_target?.target_price !== null
        ? formatCurrency(exits.weekly_value_target.target_price, 3)
        : "—";
  return `EMA penetration ${emaEntry} | Previous-day break ${breakoutEntry} | ${stop} | Weekly target ${target}`;
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
    if (statusFilter === "touched" && status !== "TOUCHED_ENTRY_PRICE")
      return false;
    if (statusFilter === "monitor" && status !== "MONITOR") return false;
    if (statusFilter === "triggered" && status !== "TRIGGERED") return false;
    if (directionFilter !== "all" && direction !== directionFilter)
      return false;
    if (query && !searchText.includes(query)) return false;
    return true;
  });
}

function renderSummary() {
  const items = state.payload?.items || [];
  const triggered = items.filter(
    (item) =>
      String(item.opportunity_status || "").toUpperCase() === "TRIGGERED",
  );
  const longCount = items.filter(
    (item) => normalizeSignalDirection(item.direction) === "LONG",
  ).length;
  const shortCount = items.length - longCount;
  const divergenceCount = items.filter((item) => item.strong_divergence).length;

  $("watchlistRailCount").textContent = `${items.length} candidates`;
  $("watchlistTriggered").textContent = String(triggered.length);
  $("watchlistDirectionMix").textContent = `${longCount} / ${shortCount}`;
  $("watchlistDivergence").textContent = String(divergenceCount);
  $("sessionHeadline").textContent =
    `Scan session: ${state.payload?.session_date || "—"}`;
  $("sessionHeadlineBody").textContent = items.length
    ? `${items.length} candidates in this session; ${triggered.length} have confirmed an entry trigger.`
    : "No qualified candidates in this session.";
}

function renderSessions() {
  const sessions = state.payload?.available_sessions || [];
  $("sessionSelect").innerHTML = sessions.length
    ? sessions
        .map(
          (session) =>
            `<option value="${escapeHtml(session.session_date)}"${session.session_date === state.payload.session_date ? " selected" : ""}>${escapeHtml(session.session_date)} · ${escapeHtml(String(session.candidate_count))} candidates</option>`,
        )
        .join("")
    : `<option value="">No sessions</option>`;

  $("sessionChips").innerHTML = sessions.length
    ? sessions
        .map((session) => {
          const active =
            session.session_date === state.payload.session_date
              ? " active"
              : "";
          return `
            <button class="session-chip${active}" type="button" data-session-date="${escapeHtml(session.session_date)}">
              ${escapeHtml(session.session_date)}
              <span class="mono"> ${escapeHtml(String(session.candidate_count))} symbols / ${escapeHtml(String(session.triggered_count || 0))} triggered</span>
            </button>
          `;
        })
        .join("")
    : `<div class="empty-state">No watchlist snapshots are stored yet. Run a scan first.</div>`;

  document.querySelectorAll("[data-session-date]").forEach((button) => {
    button.addEventListener("click", () =>
      loadWatchlist(button.dataset.sessionDate),
    );
  });
}

function renderInsights() {
  const items = state.payload?.items || [];
  const triggered = items.filter(
    (item) =>
      String(item.opportunity_status || "").toUpperCase() === "TRIGGERED",
  );
  const pending = items.filter(
    (item) =>
      String(item.opportunity_status || "").toUpperCase() !== "TRIGGERED",
  );
  const insights = [];

  if (pending.length) {
    insights.push([
      "EOD candidates are order-plan drafts",
      `${pending.length} candidates have not confirmed an entry yet. Next session, prioritize the stop-limit breakout level and EMA penetration limit.`,
    ]);
  }
  if (triggered.length) {
    insights.push([
      "Entry level confirmed",
      `${triggered.length} candidates have confirmed an entry level. Compare entry, stop, and target before execution.`,
    ]);
  }
  if (items.some((item) => item.earnings?.warning || item.earnings?.blocked)) {
    insights.push([
      "Check earnings windows",
      "Some candidates are close to earnings. Confirm event-risk rules before using the order plan.",
    ]);
  }
  if (items.some((item) => item.strong_divergence)) {
    insights.push([
      "Review strong divergence first",
      "Strong divergence does not automatically cancel the setup, but it raises exhaustion risk.",
    ]);
  }
  if (!insights.length) {
    insights.push([
      "No special notes",
      "This candidate set is clean enough for the normal review workflow.",
    ]);
  }

  $("watchlistInsights").innerHTML = insights
    .map(
      ([title, body]) =>
        `<div class="insight-item"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(body)}</p></div>`,
    )
    .join("");
}

function renderRail() {
  const items = getFilteredItems();
  $("watchlistRailCount").textContent = `${items.length} cards`;

  if (!items.length) {
    $("watchlistRailContainer").innerHTML =
      `<div class="empty-state">No candidate cards match the current filters.</div>`;
    return;
  }

  const cards = items
    .map((item) => {
      const weekly = item.weekly || {};
      const daily = item.daily || {};
      const hourly = item.hourly || {};
      const earnings = item.earnings || {};
      const earningsDisplay = getEarningsDisplay(earnings);
      const entryPlan = hourly.entry_plan || daily.entry_plan || {};
      const orderPlan = getOrderPlan(item);
      const primaryOrder = orderPlan.primary_order || {};
      const manualOrder = getPlannedOrderFor(item);
      const tags = [
        item.strong_divergence ? "Strong divergence" : "",
        earnings.warning ? "Earnings soon" : "",
        String(item.opportunity_status || "").toUpperCase() === "TRIGGERED"
          ? "Triggered"
          : "",
        String(item.opportunity_status || "").toUpperCase() === "MONITOR"
          ? "Monitor"
          : "",
        manualOrder ? `IBKR ${manualOrder.status || "Recorded"}` : "",
        ...(item.priority_tags || []),
      ].filter(Boolean);

      return `
        <article class="watchlist-rail-item">
          <div class="watchlist-rail-top">
            <div>
              <h3>${escapeHtml(item.symbol || "—")}</h3>
              <p>${getDirectionBadge(item.direction)} <span class="mono" style="font-size:11px">Score ${escapeHtml(formatNumber(item.signal_score ?? item.candidate_score ?? 0, 2))}</span></p>
            </div>
            ${getStatusBadge(item.opportunity_status)}
          </div>
          <div class="watchlist-rail-tags">${tags.length ? tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("") : `<span class="tag">Standard candidate</span>`}</div>
          <div class="watchlist-rail-body">
            <div class="watchlist-rail-reason">
              <strong>Setup context</strong>
              <p>${escapeHtml(truncateText(item.summary || daily.reason || weekly.reason, 110))}</p>
            </div>
            <div class="watchlist-rail-split">
              <div>
                <span>Weekly impulse</span>
                <strong>${escapeHtml(weekly.impulse_color || "—")} · ${escapeHtml(weekly.trend || "—")}</strong>
              </div>
              <div>
                <span>Daily Force</span>
                <strong>${escapeHtml(formatNumber(daily.force_index_ema2 ?? 0, 0))} · ${escapeHtml(daily.impulse_color || "—")}</strong>
              </div>
            </div>
            <div class="watchlist-rail-footer">
              <span>${escapeHtml(primaryOrder.stop_price !== undefined && primaryOrder.stop_price !== null ? `Stop ${formatCurrency(primaryOrder.stop_price, 2)} / Limit ${formatCurrency(primaryOrder.limit_price, 2)}` : entryPlan.ema_penetration_entry !== undefined && entryPlan.ema_penetration_entry !== null ? `EMA penetration ${formatCurrency(entryPlan.ema_penetration_entry, 3)}` : buildExecutionPlan(item))}</span>
              <span>${escapeHtml(earningsDisplay.label)}</span>
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
    $("watchlistTableContainer").innerHTML =
      `<div class="empty-state">No watchlist items match the current filters.</div>`;
    return;
  }

  const rows = items
    .map((item) => {
      const weekly = item.weekly || {};
      const daily = item.daily || {};
      const hourly = item.hourly || {};
      const exits = item.exits || {};
      const earnings = item.earnings || {};
      const earningsDisplay = getEarningsDisplay(earnings);
      const divergence = item.divergence || {};
      const manualOrder = getPlannedOrderFor(item);

      const tags = [
        item.strong_divergence ? "Strong divergence" : "",
        earnings.warning ? "Earnings soon" : "",
        earnings.blocked ? "Earnings blocked" : "",
        ...(item.priority_tags || []),
      ].filter(Boolean);

      return `
        <tr>
          <td class="symbol-cell">
            <strong>${escapeHtml(item.symbol || "—")}</strong>
            <span>${getDirectionBadge(item.direction)} <span class="mono">Score ${escapeHtml(formatNumber(item.signal_score ?? item.candidate_score ?? 0, 2))}</span></span>
          </td>
          <td>
            ${getStatusBadge(item.opportunity_status)}
            <div style="margin-top:8px">${tags.length ? tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join(" ") : `<span class="tag">Standard candidate</span>`}</div>
          </td>
          <td class="reason-cell">
            ${getReasonBlock(`Weekly impulse ${weekly.impulse_color || "—"}`, weekly.trend_score, weekly.reason)}
          </td>
          <td class="reason-cell">
            ${getReasonBlock(`Daily Force ${formatNumber(daily.force_index_ema2 ?? 0, 0)}`, daily.setup_score, daily.reason)}
          </td>
          <td class="reason-cell">
            ${getReasonBlock("Entry monitor", hourly.trigger_score, hourly.reason || buildExecutionPlan(item))}
          </td>
          <td class="reason-cell">
            <strong>${escapeHtml(earningsDisplay.label)}</strong>
            <p>${escapeHtml(earningsDisplay.reason)}</p>
          </td>
          <td class="reason-cell">
            <strong>Weekly: ${divergence.weekly?.detected ? "Divergence" : "None"} / Daily: ${divergence.daily?.detected ? "Divergence" : "None"}</strong>
            <p>${escapeHtml(
              divergence.daily?.reason ||
                divergence.weekly?.reason ||
                "No divergence notes",
            )}</p>
          </td>
          <td class="execution-cell">
            <span class="execution-inline-text">${escapeHtml(buildOrderPlanInline(item))}</span>
            <div class="mini-action-row">
              <button class="btn btn-secondary btn-mini" type="button" data-fill-order="${escapeHtml(item.symbol || "")}">Fill Record</button>
              ${manualOrder ? `<span class="tag">IBKR ${escapeHtml(manualOrder.status || "Recorded")}</span>` : `<span class="tag">No manual order</span>`}
            </div>
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
            <th>Symbol</th>
            <th>Status / Tags</th>
            <th>Weekly Filter</th>
            <th>Daily Force</th>
            <th>Entry Monitor</th>
            <th>Earnings Risk</th>
            <th>Divergence</th>
            <th>Execution Plan</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  setupHorizontalScrollbar("watchlistDetailScroll", "watchlistDetailScrollbar");
  document.querySelectorAll("[data-fill-order]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = items.find(
        (candidate) =>
          String(candidate.symbol || "") === button.dataset.fillOrder,
      );
      if (item) fillOrderFormFromCandidate(item);
    });
  });
}

function updateOrderFormVisibility() {
  const orderType = $("orderType").value;
  const showStop = orderType === "Stop Limit" || orderType === "Stop";
  const showLimit = orderType === "Stop Limit" || orderType === "Limit";
  $("orderStopPrice").style.display = showStop ? "" : "none";
  $("orderLimitPrice").style.display = showLimit ? "" : "none";
}

function fillOrderFormFromCandidate(item) {
  const plan = getOrderPlan(item);
  const primary = plan.primary_order || {};
  const secondary = plan.secondary_order || {};
  const useEma = Boolean(item.daily?.entered_value_zone);
  const order = useEma ? secondary : primary;
  $("orderSessionDate").value = state.payload?.session_date || "";
  $("orderSymbol").value = item.symbol || "";
  $("orderDirection").value = normalizeSignalDirection(item.direction);
  $("orderType").value = order.order_type || (useEma ? "Limit" : "Stop Limit");
  $("orderQuantity").value = order.quantity || "";
  $("orderStopPrice").value = useEma ? "" : (primary.stop_price ?? "");
  $("orderLimitPrice").value = order.limit_price ?? "";
  const exits = item.exits || {};
  $("orderStopLoss").value =
    exits.initial_stop_nick ?? exits.initial_stop_safezone ?? "";
  $("orderBrokerId").value = "";
  $("orderStatus").value = "SUBMITTED";
  updateOrderFormVisibility();
  $("orderSymbol").focus();
}

function renderPlannedOrders() {
  const orders = state.payload?.planned_orders || [];
  $("orderSessionDate").value = state.payload?.session_date || "";
  if (!orders.length) {
    $("plannedOrdersContainer").innerHTML =
      `<div class="empty-state compact-empty">No manual order records for this session yet.</div>`;
    return;
  }
  $("plannedOrdersContainer").innerHTML = `
    <div class="manual-order-list">
      ${orders
        .map(
          (order) => `
          <div class="manual-order-row">
            <strong>${escapeHtml(order.symbol)}</strong> ${getDirectionBadge(order.direction)}
            <span>${escapeHtml(order.order_type || "—")} ${escapeHtml(order.action || "")}</span>
            <span>Qty ${escapeHtml(formatNumber(order.quantity, 0))}</span>
            <span>${
              order.order_type === "Stop Limit"
                ? `Stop ${escapeHtml(formatCurrency(order.stop_price, 2))} / Limit ${escapeHtml(formatCurrency(order.limit_price, 2))}`
                : order.order_type === "Limit"
                  ? `Limit ${escapeHtml(formatCurrency(order.limit_price, 2))}`
                  : order.order_type === "Stop"
                    ? `Stop ${escapeHtml(formatCurrency(order.stop_price, 2))}`
                    : `${order.stop_price != null ? `Stop ${escapeHtml(formatCurrency(order.stop_price, 2))} / ` : ""}${order.limit_price != null ? `Limit ${escapeHtml(formatCurrency(order.limit_price, 2))}` : "—"}`
            }</span>
            <span>${order.stop_loss != null ? `SL ${escapeHtml(formatCurrency(order.stop_loss, 2))}` : "No SL"}</span>
            <span class="tag">${escapeHtml(order.status || "SUBMITTED")}</span>
            <button class="btn btn-secondary btn-mini" type="button" data-delete-order="${escapeHtml(order.id)}">Delete</button>
          </div>
        `,
        )
        .join("")}
    </div>
  `;
  document.querySelectorAll("[data-delete-order]").forEach((button) => {
    button.addEventListener("click", async () => {
      await apiRequest(
        `/planned-orders/${encodeURIComponent(button.dataset.deleteOrder)}`,
        { method: "DELETE" },
      );
      await loadWatchlist(state.sessionDate);
    });
  });
}

function renderFilteredViews() {
  renderRail();
  renderTable();
}

async function loadWatchlist(sessionDate = "") {
  const query = sessionDate
    ? `?session_date=${encodeURIComponent(sessionDate)}`
    : "";
  const payload = await apiRequest(`/watchlist${query}`);
  state.payload = payload;
  state.sessionDate = payload.session_date || "";
  renderSummary();
  renderSessions();
  renderInsights();
  renderPlannedOrders();
  renderFilteredViews();
}

async function bootApp() {
  syncShell("watchlist");
  setScreenState(
    "boot",
    "Checking the local Journal API and loading the watchlist...",
  );
  try {
    const health = await ensureApiReady();
    renderConnectionStatus(
      true,
      `Local API connected · ${health.server.host}:${health.server.port}`,
    );
    setScreenState("app");
    await loadWatchlist();
  } catch (error) {
    renderConnectionStatus(false, "Local API unavailable");
    $("configError").textContent = error.message || String(error);
    setScreenState("config");
  }
}

function bindEvents() {
  $("retryConnectBtn").addEventListener("click", bootApp);
  $("refreshWatchlistBtn").addEventListener("click", () =>
    loadWatchlist(state.sessionDate),
  );
  $("sessionSelect").addEventListener("change", (event) =>
    loadWatchlist(event.target.value),
  );
  $("statusFilter").addEventListener("change", renderFilteredViews);
  $("directionFilter").addEventListener("change", renderFilteredViews);
  $("watchlistSearch").addEventListener("input", renderFilteredViews);
  $("orderType").addEventListener("change", updateOrderFormVisibility);
  updateOrderFormVisibility();
  $("plannedOrderForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const direction = $("orderDirection").value;
    await apiRequest("/planned-orders", {
      method: "POST",
      body: {
        session_date:
          $("orderSessionDate").value || state.payload?.session_date || "",
        symbol: $("orderSymbol").value,
        direction,
        broker: "IBKR",
        broker_order_id: $("orderBrokerId").value || null,
        order_type: $("orderType").value,
        action: direction === "SHORT" ? "SELL" : "BUY",
        quantity: parseNumberValue($("orderQuantity").value),
        stop_price: parseNumberValue($("orderStopPrice").value),
        limit_price: parseNumberValue($("orderLimitPrice").value),
        stop_loss: parseNumberValue($("orderStopLoss").value),
        status: $("orderStatus").value,
      },
    });
    $("plannedOrderForm").reset();
    updateOrderFormVisibility();
    await loadWatchlist(state.sessionDate);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  bootApp();
});
