import {
  apiRequest,
  ensureApiReady,
  escapeHtml,
  renderConnectionStatus,
  setScreenState,
  syncShell,
} from "./shared.js";

const state = {
  payload: null,
  loading: false,
  models: [],
  activeModelId: "",
};

const AI_ANALYSIS_ENABLED = false;

function $(id) {
  return document.getElementById(id);
}

function getBadge(label, tone = "info") {
  const toneClass = ["safe", "danger", "warn", "info"].includes(tone) ? tone : "info";
  return `<span class="badge badge-${toneClass}">${escapeHtml(label || "—")}</span>`;
}

function setLoading(loading) {
  state.loading = loading;
  $("analyzeBtn").disabled = loading;
  $("analyzeBtn").textContent = loading ? "Analyzing..." : "Start Analysis";
}

function renderPromptOutline(outline = []) {
  const items = outline.length
    ? outline
    : [
        "Weekly rules check impulse-system color, MACD slope, EMA slope, and confirmed bars; the impulse system only blocks forbidden trade directions.",
        "Daily rules focus on the 2-day Force Index EMA; RSI, histogram, and candle shape are supporting context only.",
        "The execution section shows the EMA penetration reference price, the alternate previous-day high/low breakout trigger, stops, and weekly value-zone target.",
      ];

  $("analysisPromptOutline").innerHTML = items
    .map((item) => `<div class="insight-item"><strong>System Rules</strong><p>${escapeHtml(item)}</p></div>`)
    .join("");
}

function renderMetrics(containerId, metrics = []) {
  $(containerId).innerHTML = metrics.length
    ? metrics
        .map(
          (item) => `
            <div class="analysis-metric-card">
              <span>${escapeHtml(item.label || "—")}</span>
              <strong class="metric-${escapeHtml(item.emphasis || "neutral")}">${escapeHtml(item.value || "—")}</strong>
            </div>
          `
        )
        .join("")
    : `<div class="empty-state">No metrics yet.</div>`;
}

function renderChecks(containerId, checks = []) {
  $(containerId).innerHTML = checks.length
    ? checks
        .map(
          (item) => `
            <div class="analysis-check-item">
              ${getBadge(item.pass ? "Pass" : "Fail", item.pass ? "safe" : "warn")}
              <strong>${escapeHtml(item.label || "—")}</strong>
              <p>${escapeHtml(item.detail || "—")}</p>
            </div>
          `
        )
        .join("")
    : `<div class="empty-state">No checks yet.</div>`;
}

function renderStopMethods(containerId, methods = []) {
  $(containerId).innerHTML = methods.length
    ? methods
        .map(
          (item) => `
            <div class="analysis-check-item">
              ${getBadge(item.auto ? "Quantified" : "Manual", item.auto ? "info" : "warn")}
              <strong>${escapeHtml(item.label || "—")}</strong>
              <p>Stop Price: ${escapeHtml(item.price || "—")}</p>
              <p>Use Case: ${escapeHtml(item.suitable_for || "—")}</p>
              <p>Reference: ${escapeHtml(item.reference || "—")}</p>
              <p>${escapeHtml(item.detail || "—")}</p>
            </div>
          `
        )
        .join("")
    : `<div class="empty-state">No stop methods yet.</div>`;
}

function findMetric(metrics, label) {
  return (metrics || []).find((item) => item.label === label)?.value || "—";
}

function renderSystem(system) {
  const recommendation = system?.recommendation || {};
  const weekly = system?.weekly || {};
  const daily = system?.daily || {};
  const execution = system?.execution || {};
  const divergence = system?.divergence || {};
  const keyLevels = system?.key_levels || {};
  const stopMethods = system?.stop_methods || {};

  $("summarySymbol").textContent = system?.symbol || "—";
  $("summaryClose").textContent = findMetric(keyLevels.metrics, "Latest Close");
  $("summarySystemDecision").textContent = recommendation.label || "—";
  $("summarySystemReason").textContent = recommendation.reason || "Waiting for analysis results.";
  $("summaryEntry").textContent = execution?.entry_price ?? "—";
  $("summaryEntryReason").textContent = execution?.summary || "Waiting for execution levels.";
  $("summaryStop").textContent = execution?.stop_loss ?? "—";
  $("summaryStopReason").textContent = execution?.summary || "Waiting for stop levels.";
  $("systemDecisionBadge").innerHTML = getBadge(recommendation.label || "System Decision", recommendation.tone || "info");
  $("systemSummary").className = `alert ${recommendation.tone === "safe" ? "success" : recommendation.tone === "warn" ? "warn" : "info"}`;
  $("systemSummary").textContent = system?.summary || "No system decision yet.";
  const model = system?.model || {};
  $("systemDifference").innerHTML = `
    <div class="insight-item">
      <strong>Model Definition</strong>
      <p>${escapeHtml(model.label || "Current model")}</p>
      <p>${escapeHtml(model.description || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>System Watch Decision</strong>
      <p>${escapeHtml(recommendation.reason || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>System Method</strong>
      <p>Weekly and daily decisions reuse the existing backend rules without adding discretionary filters.</p>
    </div>
  `;

  $("weeklyTitle").textContent = weekly.title || "Weekly / Trend";
  $("weeklySubtitle").textContent = weekly.subtitle || "—";
  $("weeklyReason").textContent = weekly.reason || "—";
  renderMetrics("weeklyMetrics", weekly.metrics);
  renderChecks("weeklyChecks", weekly.checks);

  $("dailyTitle").textContent = daily.title || "Daily / Setup";
  $("dailySubtitle").textContent = daily.subtitle || "—";
  $("dailyReason").textContent = daily.reason || "—";
  renderMetrics("dailyMetrics", daily.metrics);
  renderChecks("dailyChecks", daily.checks);

  $("executionTitle").textContent = execution.title || "Execution Plan / Entry and Stops";
  $("executionSubtitle").textContent = system?.model?.intraday_trigger || "Shows the current model trigger, stop, and target levels.";
  $("executionReason").textContent = execution.summary || "—";
  renderMetrics("executionMetrics", execution.metrics);

  $("divergenceReason").textContent = divergence.summary || "—";
  renderMetrics("divergenceMetrics", divergence.metrics);
  renderMetrics("keyLevelMetrics", keyLevels.metrics);
  $("stopMethodsSummary").textContent = stopMethods.summary || "—";
  renderStopMethods("initialStopMethodsList", stopMethods.initial_methods || []);
  renderStopMethods("trailingStopMethodsList", stopMethods.trailing_methods || []);
}

function renderAi(ai) {
  if (!AI_ANALYSIS_ENABLED) return;

  renderPromptOutline(ai?.outline || []);

  if (!ai || ai.status === "SKIPPED") {
    $("summaryAiDecision").textContent = "Disabled";
    $("summaryAiReason").textContent = ai?.message || "AI was not enabled for this run.";
    $("aiStatusBadge").innerHTML = getBadge("AI Disabled", "info");
    $("aiSummary").className = "alert info";
    $("aiSummary").textContent = ai?.message || "AI was not enabled for this run.";
    $("aiDifference").innerHTML = "";
    return;
  }

  if (ai.status === "UNAVAILABLE") {
    $("summaryAiDecision").textContent = "Not Configured";
    $("summaryAiReason").textContent = ai.message || "AI model is not configured.";
    $("aiStatusBadge").innerHTML = getBadge("AI Not Configured", "warn");
    $("aiSummary").className = "alert warn";
    $("aiSummary").textContent = ai.message || "AI model is not configured.";
    $("aiDifference").innerHTML = "";
    return;
  }

  if (ai.status === "ERROR") {
    $("summaryAiDecision").textContent = "Call Failed";
    $("summaryAiReason").textContent = ai.message || "AI analysis failed.";
    $("aiStatusBadge").innerHTML = getBadge("AI Failed", "danger");
    $("aiSummary").className = "alert danger";
    $("aiSummary").textContent = ai.message || "AI analysis failed.";
    $("aiDifference").innerHTML = "";
    return;
  }

  if (ai.status === "RAW") {
    $("summaryAiDecision").textContent = "Raw Response";
    $("summaryAiReason").textContent = ai.message || "AI returned unstructured content.";
    $("aiStatusBadge").innerHTML = getBadge(ai.model || "AI Raw", "info");
    $("aiSummary").className = "alert info";
    $("aiSummary").textContent = ai.raw_text || "AI returned no displayable content.";
    $("aiDifference").innerHTML = "";
    return;
  }

  const structured = ai.structured || {};
  const difference = structured.difference_vs_system || {};
  const investmentView = structured.investment_view || {};
  const weeklyAnalysis = structured.weekly_analysis || {};
  const dailyAnalysis = structured.daily_analysis || {};

  $("summaryAiDecision").textContent = structured.watch_decision || "—";
  $("summaryAiReason").textContent = investmentView.summary || difference.agreement || "AI returned structured analysis.";
  $("aiStatusBadge").innerHTML = getBadge(`${ai.model || "AI"} · ${structured.watch_decision || "Completed"}`, "safe");
  $("aiSummary").className = "alert success";
  $("aiSummary").textContent = investmentView.summary || "AI returned structured analysis.";

  const weeklySignals = Array.isArray(weeklyAnalysis.signals) ? weeklyAnalysis.signals : [];
  const dailySignals = Array.isArray(dailyAnalysis.signals) ? dailyAnalysis.signals : [];
  const riskControls = Array.isArray(investmentView.risk_controls) ? investmentView.risk_controls : [];
  const keyLevelFocus = Array.isArray(investmentView.key_level_focus) ? investmentView.key_level_focus : [];
  const differences = Array.isArray(difference.differences) ? difference.differences : [];

  $("aiDifference").innerHTML = `
    <div class="insight-item">
      <strong>AI Weekly View</strong>
      <p>${escapeHtml(weeklyAnalysis.summary || "—")}</p>
      <p>${escapeHtml(weeklySignals.join(";  ") || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>AI Daily View</strong>
      <p>${escapeHtml(dailyAnalysis.summary || "—")}</p>
      <p>${escapeHtml(dailySignals.join(";  ") || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>Difference vs System</strong>
      <p>${escapeHtml(difference.agreement || "—")}</p>
      <p>${escapeHtml(differences.join(";  ") || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>AI Risk Controls and Focus</strong>
      <p>${escapeHtml(riskControls.join(";  ") || "—")}</p>
      <p>${escapeHtml(keyLevelFocus.join(";  ") || "—")}</p>
    </div>
  `;
}

function renderPayload(payload) {
  state.payload = payload;
  const system = payload?.system || {};
  const ai = payload?.ai || {};

  $("analysisHeadline").textContent = `${payload?.symbol || "—"} · System Technicals`;
  $("analysisHeadlineBody").textContent = `Generated At: ${escapeHtml(payload?.generated_at || "—")}. Currently showing system-rule analysis only.`;
  renderSystem(system);
  if (AI_ANALYSIS_ENABLED) renderAi(ai);
}

async function loadAnalysis(symbol) {
  setLoading(true);
  $("systemSummary").className = "alert info";
  $("systemSummary").textContent = `Analyzing ${symbol} weekly and daily structure...`;
  if (AI_ANALYSIS_ENABLED) {
    $("aiSummary").className = "alert info";
    $("aiSummary").textContent = $("includeAiToggle").checked ? "Requesting AI comparison analysis..." : "AI was not enabled for this run.";
  }

  try {
    const payload = await apiRequest("/technical-analysis", {
      method: "POST",
      body: {
        symbol,
        include_ai: AI_ANALYSIS_ENABLED && $("includeAiToggle")?.checked,
        model_id: $("modelSelect")?.value || state.activeModelId || null,
      },
    });
    renderPayload(payload);
  } catch (error) {
    $("systemSummary").className = "alert danger";
    $("systemSummary").textContent = error.message || String(error);
    if (AI_ANALYSIS_ENABLED) {
      $("aiSummary").className = "alert warn";
      $("aiSummary").textContent = "No displayable AI result for this run.";
    }
  } finally {
    setLoading(false);
  }
}

async function loadModels() {
  const payload = await apiRequest("/trading-models");
  state.models = Array.isArray(payload.models) ? payload.models : [];
  state.activeModelId = payload.active_model_id || state.models[0]?.id || "";
  $("modelSelect").innerHTML = state.models
    .map((model) => `<option value="${escapeHtml(model.id)}">${escapeHtml(model.label)}</option>`)
    .join("");
  if (state.activeModelId) {
    $("modelSelect").value = state.activeModelId;
  }
}

async function bootApp() {
  syncShell("analysis");
  setScreenState("boot", "Checking local Journal API and preparing the technical-analysis page...");
  try {
    const health = await ensureApiReady();
    renderConnectionStatus(true, `Local API connected · ${health.server.host}:${health.server.port}`);
    setScreenState("app");
    await loadModels();
    renderPromptOutline();

    const initialSymbol = new URLSearchParams(window.location.search).get("symbol");
    if (initialSymbol) {
      $("symbolInput").value = initialSymbol.toUpperCase();
      await loadAnalysis(initialSymbol.toUpperCase());
    }
  } catch (error) {
    renderConnectionStatus(false, "Local API unavailable");
    $("configError").textContent = error.message || String(error);
    setScreenState("config");
  }
}

function bindEvents() {
  $("retryConnectBtn").addEventListener("click", bootApp);
  $("analysisForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const symbol = $("symbolInput").value.trim().toUpperCase();
    $("symbolInput").value = symbol;
    if (!symbol) {
      $("systemSummary").className = "alert warn";
      $("systemSummary").textContent = "Enter a ticker first.";
      return;
    }
    await loadAnalysis(symbol);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  bootApp();
});
