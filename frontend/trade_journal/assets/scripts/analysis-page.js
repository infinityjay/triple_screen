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
  $("analyzeBtn").textContent = loading ? "分析中…" : "开始分析";
}

function renderPromptOutline(outline = []) {
  const items = outline.length
    ? outline
    : [
        "周线看动力系统颜色、MACD 斜率、EMA 斜率和确认 bars；动力系统只负责禁止交易方向。",
        "日线核心看 2日 Force Index EMA，RSI、Histogram、K线形态只作为辅助信息。",
        "执行区展示 EMA 穿透参考价、前日高/低点外一跳的替代触发价、止损和周线价值区间目标。",
      ];

  $("analysisPromptOutline").innerHTML = items
    .map((item) => `<div class="insight-item"><strong>系统规则</strong><p>${escapeHtml(item)}</p></div>`)
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
    : `<div class="empty-state">暂无指标。</div>`;
}

function renderChecks(containerId, checks = []) {
  $(containerId).innerHTML = checks.length
    ? checks
        .map(
          (item) => `
            <div class="analysis-check-item">
              ${getBadge(item.pass ? "通过" : "未通过", item.pass ? "safe" : "warn")}
              <strong>${escapeHtml(item.label || "—")}</strong>
              <p>${escapeHtml(item.detail || "—")}</p>
            </div>
          `
        )
        .join("")
    : `<div class="empty-state">暂无检查结果。</div>`;
}

function renderStopMethods(containerId, methods = []) {
  $(containerId).innerHTML = methods.length
    ? methods
        .map(
          (item) => `
            <div class="analysis-check-item">
              ${getBadge(item.auto ? "可量化" : "手工判断", item.auto ? "info" : "warn")}
              <strong>${escapeHtml(item.label || "—")}</strong>
              <p>止损位：${escapeHtml(item.price || "—")}</p>
              <p>适用：${escapeHtml(item.suitable_for || "—")}</p>
              <p>参考：${escapeHtml(item.reference || "—")}</p>
              <p>${escapeHtml(item.detail || "—")}</p>
            </div>
          `
        )
        .join("")
    : `<div class="empty-state">暂无止损方法。</div>`;
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
  $("summaryClose").textContent = findMetric(keyLevels.metrics, "最新收盘");
  $("summarySystemDecision").textContent = recommendation.label || "—";
  $("summarySystemReason").textContent = recommendation.reason || "等待分析结果。";
  $("summaryEntry").textContent = execution?.entry_price ?? "—";
  $("summaryEntryReason").textContent = execution?.summary || "等待执行价位。";
  $("summaryStop").textContent = execution?.stop_loss ?? "—";
  $("summaryStopReason").textContent = execution?.summary || "等待止损价位。";
  $("systemDecisionBadge").innerHTML = getBadge(recommendation.label || "系统结论", recommendation.tone || "info");
  $("systemSummary").className = `alert ${recommendation.tone === "safe" ? "success" : recommendation.tone === "warn" ? "warn" : "info"}`;
  $("systemSummary").textContent = system?.summary || "暂无系统结论。";
  const model = system?.model || {};
  $("systemDifference").innerHTML = `
    <div class="insight-item">
      <strong>模型口径</strong>
      <p>${escapeHtml(model.label || "当前配置模型")}</p>
      <p>${escapeHtml(model.description || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>系统观察建议</strong>
      <p>${escapeHtml(recommendation.reason || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>系统口径说明</strong>
      <p>周线和日线判断完全复用已有后端函数，不额外混入主观筛选。</p>
    </div>
  `;

  $("weeklyTitle").textContent = weekly.title || "周线 / 趋势";
  $("weeklySubtitle").textContent = weekly.subtitle || "—";
  $("weeklyReason").textContent = weekly.reason || "—";
  renderMetrics("weeklyMetrics", weekly.metrics);
  renderChecks("weeklyChecks", weekly.checks);

  $("dailyTitle").textContent = daily.title || "日线 / Setup";
  $("dailySubtitle").textContent = daily.subtitle || "—";
  $("dailyReason").textContent = daily.reason || "—";
  renderMetrics("dailyMetrics", daily.metrics);
  renderChecks("dailyChecks", daily.checks);

  $("executionTitle").textContent = execution.title || "执行计划 / 买入与止损";
  $("executionSubtitle").textContent = system?.model?.intraday_trigger || "展示当前模型的触发价、止损与目标。";
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
    $("summaryAiDecision").textContent = "未启用";
    $("summaryAiReason").textContent = ai?.message || "本次未启用 AI。";
    $("aiStatusBadge").innerHTML = getBadge("AI 未启用", "info");
    $("aiSummary").className = "alert info";
    $("aiSummary").textContent = ai?.message || "本次未启用 AI。";
    $("aiDifference").innerHTML = "";
    return;
  }

  if (ai.status === "UNAVAILABLE") {
    $("summaryAiDecision").textContent = "未配置";
    $("summaryAiReason").textContent = ai.message || "AI 模型尚未配置。";
    $("aiStatusBadge").innerHTML = getBadge("AI 未配置", "warn");
    $("aiSummary").className = "alert warn";
    $("aiSummary").textContent = ai.message || "AI 模型尚未配置。";
    $("aiDifference").innerHTML = "";
    return;
  }

  if (ai.status === "ERROR") {
    $("summaryAiDecision").textContent = "调用失败";
    $("summaryAiReason").textContent = ai.message || "AI 分析失败。";
    $("aiStatusBadge").innerHTML = getBadge("AI 失败", "danger");
    $("aiSummary").className = "alert danger";
    $("aiSummary").textContent = ai.message || "AI 分析失败。";
    $("aiDifference").innerHTML = "";
    return;
  }

  if (ai.status === "RAW") {
    $("summaryAiDecision").textContent = "原文返回";
    $("summaryAiReason").textContent = ai.message || "AI 返回了非结构化内容。";
    $("aiStatusBadge").innerHTML = getBadge(ai.model || "AI 原文", "info");
    $("aiSummary").className = "alert info";
    $("aiSummary").textContent = ai.raw_text || "AI 未返回可展示内容。";
    $("aiDifference").innerHTML = "";
    return;
  }

  const structured = ai.structured || {};
  const difference = structured.difference_vs_system || {};
  const investmentView = structured.investment_view || {};
  const weeklyAnalysis = structured.weekly_analysis || {};
  const dailyAnalysis = structured.daily_analysis || {};

  $("summaryAiDecision").textContent = structured.watch_decision || "—";
  $("summaryAiReason").textContent = investmentView.summary || difference.agreement || "AI 已返回结构化分析。";
  $("aiStatusBadge").innerHTML = getBadge(`${ai.model || "AI"} · ${structured.watch_decision || "已完成"}`, "safe");
  $("aiSummary").className = "alert success";
  $("aiSummary").textContent = investmentView.summary || "AI 已返回结构化分析。";

  const weeklySignals = Array.isArray(weeklyAnalysis.signals) ? weeklyAnalysis.signals : [];
  const dailySignals = Array.isArray(dailyAnalysis.signals) ? dailyAnalysis.signals : [];
  const riskControls = Array.isArray(investmentView.risk_controls) ? investmentView.risk_controls : [];
  const keyLevelFocus = Array.isArray(investmentView.key_level_focus) ? investmentView.key_level_focus : [];
  const differences = Array.isArray(difference.differences) ? difference.differences : [];

  $("aiDifference").innerHTML = `
    <div class="insight-item">
      <strong>AI 周线观点</strong>
      <p>${escapeHtml(weeklyAnalysis.summary || "—")}</p>
      <p>${escapeHtml(weeklySignals.join("； ") || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>AI 日线观点</strong>
      <p>${escapeHtml(dailyAnalysis.summary || "—")}</p>
      <p>${escapeHtml(dailySignals.join("； ") || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>与系统的差异</strong>
      <p>${escapeHtml(difference.agreement || "—")}</p>
      <p>${escapeHtml(differences.join("； ") || "—")}</p>
    </div>
    <div class="insight-item">
      <strong>AI 风控与关注点</strong>
      <p>${escapeHtml(riskControls.join("； ") || "—")}</p>
      <p>${escapeHtml(keyLevelFocus.join("； ") || "—")}</p>
    </div>
  `;
}

function renderPayload(payload) {
  state.payload = payload;
  const system = payload?.system || {};
  const ai = payload?.ai || {};

  $("analysisHeadline").textContent = `${payload?.symbol || "—"} · 系统技术面`;
  $("analysisHeadlineBody").textContent = `生成时间：${escapeHtml(payload?.generated_at || "—")}。当前只展示系统规则分析。`;
  renderSystem(system);
  if (AI_ANALYSIS_ENABLED) renderAi(ai);
}

async function loadAnalysis(symbol) {
  setLoading(true);
  $("systemSummary").className = "alert info";
  $("systemSummary").textContent = `正在分析 ${symbol} 的周线与日线…`;
  if (AI_ANALYSIS_ENABLED) {
    $("aiSummary").className = "alert info";
    $("aiSummary").textContent = $("includeAiToggle").checked ? "正在请求 AI 对照分析…" : "本次未启用 AI。";
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
      $("aiSummary").textContent = "本次没有可展示的 AI 结果。";
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
  setScreenState("boot", "检查本地 Journal API，并准备技术分析页面…");
  try {
    const health = await ensureApiReady();
    renderConnectionStatus(true, `本地 API 已连接 · ${health.server.host}:${health.server.port}`);
    setScreenState("app");
    await loadModels();
    renderPromptOutline();

    const initialSymbol = new URLSearchParams(window.location.search).get("symbol");
    if (initialSymbol) {
      $("symbolInput").value = initialSymbol.toUpperCase();
      await loadAnalysis(initialSymbol.toUpperCase());
    }
  } catch (error) {
    renderConnectionStatus(false, "本地 API 不可用");
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
      $("systemSummary").textContent = "请先输入股票代码。";
      return;
    }
    await loadAnalysis(symbol);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  bootApp();
});
