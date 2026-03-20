import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  fetchAppInfo,
  fetchAuthStatus,
  fetchModels,
  fetchReviewEstimate,
  shutdownApp,
  startReview,
  validateAuth,
} from "./api/client.js";
import { AuthStatusCard } from "./components/AuthStatusCard.jsx";
import { CommandRail } from "./components/CommandRail.jsx";
import { Masthead } from "./components/Masthead.jsx";
import { ModelRouterPanel } from "./components/ModelRouterPanel.jsx";
import { ReportWorkspace } from "./components/ReportWorkspace.jsx";
import { RunLedger } from "./components/RunLedger.jsx";
import { SynthesisPanel } from "./components/SynthesisPanel.jsx";
import { StatusRibbon } from "./components/StatusRibbon.jsx";
import { TaskInput } from "./components/TaskInput.jsx";
import { useSSE } from "./hooks/useSSE.js";
import { ThemeProvider, useTheme } from "./ThemeContext.jsx";

// ── State shape ───────────────────────────────────────────────────────────────

const GENERAL_AGENT_ROLES = ["reviewer_1", "reviewer_2", "reviewer_3"];
const STRICT_AGENT_ROLES = [
  "spec_drift",
  "architecture_integrity",
  "security_boundary",
  "runtime_operational",
  "test_integrity",
  "llm_artifact_simplification",
  "challenger",
];
const FINAL_AGENT_ROLES = new Set(["synthesizer", "judge"]);
const STRICT_AGENT_LABELS = {
  spec_drift: "規格漂移",
  architecture_integrity: "架構完整性",
  security_boundary: "安全邊界",
  runtime_operational: "執行期與營運",
  test_integrity: "測試完整性",
  llm_artifact_simplification: "LLM 產物與簡化",
  challenger: "挑戰者",
  judge: "最終裁決",
};
const REVIEWER_DISPLAY_NAMES = ["架構審查", "後端審查", "前端與體驗審查"];

function agentOrderForProfile(profile) {
  return profile === "llm_repo" ? STRICT_AGENT_ROLES : GENERAL_AGENT_ROLES;
}

function displayNameForAgent(agent, reviewerNames) {
  if (STRICT_AGENT_LABELS[agent]) return STRICT_AGENT_LABELS[agent];
  const labels = {
    reviewer_1: reviewerNames[0] ?? "架構審查",
    reviewer_2: reviewerNames[1] ?? "後端審查",
    reviewer_3: reviewerNames[2] ?? "前端與體驗審查",
    synthesizer: "最終報告整合",
    judge: "最終裁決",
    orchestrator: "協調規劃",
  };
  return labels[agent] ?? agent;
}

function resolveContextWindowTokens(modelId, models) {
  if (!modelId) return null;
  const model = models.find((m) => m.id === modelId);
  const n = model?.capabilities?.limits?.max_context_window_tokens;
  return Number.isFinite(n) && n > 0 ? n : null;
}

function reviewModeConfig(reviewProfile) {
  return reviewProfile === "llm_repo"
    ? {
        review_profile: "llm_repo",
        evidence_mode: "static_runtime",
        output_mode: "structured_report",
        gate_mode: "blocking",
        convergence_mode: "adaptive_rerun",
      }
    : {
        review_profile: "general",
        evidence_mode: "static_first",
        output_mode: "report",
        gate_mode: "advisory",
        convergence_mode: "single_pass",
      };
}

function makeAgentState() {
  return {
    status: "idle",       // idle | running | done | error
    streamText: "",
    streaming: false,
    model: null,
    toolCalls: [],
    error: null,
    plan: null,           // orchestrator only — ReviewPlan once submitted
    displayName: null,
  };
}

function makeSynthState() {
  return {
    status: "idle",
    streamText: "",
    streaming: false,
    model: null,
    role: "synthesizer",
    toolCalls: [],
  };
}

const initialState = {
  reviewStatus: "idle",    // idle | running | complete | error
  reviewProfile: "general",
  sseUrl: null,
  reviewId: null,
  agentOrder: GENERAL_AGENT_ROLES,
  agents: Object.fromEntries(GENERAL_AGENT_ROLES.map((r) => [r, makeAgentState()])),
  orchestrator: makeAgentState(),
  synthesis: makeSynthState(),
  metrics: {},             // { [agentRole]: { input_tokens, output_tokens, turns, quota, model, context_window_tokens } }
  verificationSummary: null,
  findings: [],
  consensusFindings: [],
  disputedFindings: [],
  convergenceMetrics: null,
  driftSummary: null,
  verdict: null,
  sessionReports: {},
  finalSummaryMarkdown: "",
  nextStepsMarkdown: "",
  artifactSummary: null,
  globalError: null,
  timers: {
    reviewStartedAt: null,   // ms — set when review is submitted
    reviewDoneAt: null,      // ms — set when stream ends
    agents: {},              // [agent]: { startedAt, doneAt }
  },
};

// ── Reducer ───────────────────────────────────────────────────────────────────

function reducer(state, action) {
  switch (action.type) {
    case "REVIEW_STARTED":
      return {
        ...initialState,
        reviewStatus: "running",
        reviewProfile: action.reviewProfile || "general",
        sseUrl: action.sseUrl,
        reviewId: action.reviewId,
        agentOrder: agentOrderForProfile(action.reviewProfile || "general"),
        agents: Object.fromEntries(
          agentOrderForProfile(action.reviewProfile || "general").map((r) => [r, makeAgentState()])
        ),
        orchestrator: makeAgentState(),
        synthesis: makeSynthState(),
        metrics: {},
        timers: {
          reviewStartedAt: action.timestamp,
          reviewDoneAt: null,
          agents: {},
        },
      };

    case "AGENT_STARTED": {
      let next;
      if (FINAL_AGENT_ROLES.has(action.agent)) {
        next = {
          ...state,
          synthesis: {
            ...state.synthesis,
            role: action.agent,
            status: "running",
            model: action.model,
            streaming: true,
          },
        };
      } else if (action.agent === "orchestrator") {
        next = {
          ...state,
          orchestrator: {
            ...state.orchestrator,
            status: "running",
            model: action.model,
            displayName: action.displayName || "協調規劃",
          },
        };
      } else {
        const prevAgent = state.agents[action.agent] || makeAgentState();
        next = {
          ...state,
          agents: {
            ...state.agents,
            [action.agent]: {
              ...prevAgent,
              status: "running",
              model: action.model,
              displayName: action.displayName || prevAgent.displayName || action.agent,
            },
          },
          agentOrder: state.agentOrder.includes(action.agent)
            ? state.agentOrder
            : [...state.agentOrder, action.agent],
        };
      }
      const prevMetrics = state.metrics[action.agent] || {};
      return {
        ...next,
        metrics: {
          ...next.metrics,
          [action.agent]: {
            input_tokens: prevMetrics.input_tokens ?? 0,
            output_tokens: prevMetrics.output_tokens ?? 0,
            turns: prevMetrics.turns ?? 0,
            quota: prevMetrics.quota ?? null,
            model: action.model || prevMetrics.model || null,
            context_window_tokens:
              action.context_window_tokens ?? prevMetrics.context_window_tokens ?? null,
          },
        },
        timers: {
          ...next.timers,
          agents: { ...next.timers.agents, [action.agent]: { startedAt: action.timestamp, doneAt: null } },
        },
      };
    }

    case "AGENT_STREAM": {
      if (FINAL_AGENT_ROLES.has(action.agent)) {
        return {
          ...state,
          synthesis: {
            ...state.synthesis,
            role: action.agent,
            streamText: state.synthesis.streamText + action.content,
            streaming: true,
          },
        };
      }
      if (action.agent === "orchestrator") {
        return {
          ...state,
          orchestrator: {
            ...state.orchestrator,
            streamText: state.orchestrator.streamText + action.content,
            streaming: true,
          },
        };
      }
      const prev = state.agents[action.agent] || makeAgentState();
      return {
        ...state,
        agents: {
          ...state.agents,
          [action.agent]: { ...prev, streamText: prev.streamText + action.content, streaming: true },
        },
      };
    }

    case "AGENT_TOOL_CALL": {
      const isTrackedAgent =
        action.agent === "orchestrator" || FINAL_AGENT_ROLES.has(action.agent) || state.agentOrder.includes(action.agent);
      if (!isTrackedAgent) return state;
      if (FINAL_AGENT_ROLES.has(action.agent)) {
        return {
          ...state,
          synthesis: {
            ...state.synthesis,
            toolCalls: [...(state.synthesis.toolCalls || []), { tool_name: action.tool_name, args: action.args }],
          },
        };
      }
      const key = action.agent === "orchestrator" ? "orchestrator" : action.agent;
      const target = key === "orchestrator" ? state.orchestrator : (state.agents[key] || makeAgentState());
      const updated = { ...target, toolCalls: [...target.toolCalls, { tool_name: action.tool_name, args: action.args }] };
      return key === "orchestrator"
        ? { ...state, orchestrator: updated }
        : { ...state, agents: { ...state.agents, [key]: updated } };
    }

    case "AGENT_DONE": {
      let next;
      if (FINAL_AGENT_ROLES.has(action.agent)) {
        next = {
          ...state,
          synthesis: { ...state.synthesis, role: action.agent, status: "done", streaming: false },
        };
      } else if (action.agent === "orchestrator") {
        next = { ...state, orchestrator: { ...state.orchestrator, status: "done", streaming: false } };
      } else {
        next = {
          ...state,
          agents: {
            ...state.agents,
            [action.agent]: { ...state.agents[action.agent], status: "done", streaming: false },
          },
        };
      }
      return {
        ...next,
        timers: {
          ...next.timers,
          agents: {
            ...next.timers.agents,
            [action.agent]: { ...(next.timers.agents[action.agent] || {}), doneAt: action.timestamp },
          },
        },
      };
    }

    case "AGENT_ERROR": {
      if (FINAL_AGENT_ROLES.has(action.agent)) {
        return { ...state, synthesis: { ...state.synthesis, role: action.agent, status: "error" } };
      }
      const agentToUpdate =
        action.agent === "orchestrator" || state.agentOrder.includes(action.agent) ? action.agent : null;
      if (!agentToUpdate) return state;
      if (agentToUpdate === "orchestrator") {
        return {
          ...state,
          orchestrator: { ...state.orchestrator, status: "error", error: action.error },
        };
      }
      return {
        ...state,
        agents: {
          ...state.agents,
          [agentToUpdate]: { ...state.agents[agentToUpdate], status: "error", error: action.error },
        },
      };
    }

    case "METRICS_UPDATE": {
      const prev = state.metrics[action.agent] || {};
      return {
        ...state,
        metrics: {
          ...state.metrics,
          [action.agent]: {
            input_tokens: action.input_tokens ?? prev.input_tokens ?? 0,
            output_tokens: action.output_tokens ?? prev.output_tokens ?? 0,
            turns: action.turns ?? prev.turns ?? 0,
            quota: action.quota ?? prev.quota ?? null,
            model: action.model || prev.model || null,
            context_window_tokens:
              action.context_window_tokens ?? prev.context_window_tokens ?? null,
          },
        },
      };
    }

    case "ORCHESTRATOR_PLAN":
      return {
        ...state,
        orchestrator: { ...state.orchestrator, plan: action.plan },
      };

    case "VERIFICATION_UPDATED":
      return {
        ...state,
        verificationSummary: action.summary,
      };

    case "FINDING_EMITTED":
      return {
        ...state,
        findings: [...state.findings, action.finding],
      };

    case "JUDGE_SUMMARY":
      return {
        ...state,
        verdict: action.summary.verdict || state.verdict,
        consensusFindings: action.summary.consensus_findings || [],
        disputedFindings: action.summary.disputed_findings || [],
        convergenceMetrics: action.summary.convergence_metrics || null,
        driftSummary: action.summary.drift_summary || null,
      };

    case "REVIEW_VERDICT":
      return {
        ...state,
        verdict: action.verdict,
      };

    case "REVIEW_COMPLETE":
      return {
        ...state,
        reviewStatus: "complete",
        verdict: action.verdict ?? state.verdict,
        findings: action.findings ?? state.findings,
        consensusFindings: action.consensusFindings ?? state.consensusFindings,
        disputedFindings: action.disputedFindings ?? state.disputedFindings,
        convergenceMetrics: action.convergenceMetrics ?? state.convergenceMetrics,
        verificationSummary: action.verificationSummary ?? state.verificationSummary,
        driftSummary: action.driftSummary ?? state.driftSummary,
        sessionReports: action.sessionReports ?? state.sessionReports,
        finalSummaryMarkdown: action.finalSummaryMarkdown ?? state.finalSummaryMarkdown,
        nextStepsMarkdown: action.nextStepsMarkdown ?? state.nextStepsMarkdown,
        artifactSummary: action.artifactSummary ?? state.artifactSummary,
        synthesis: action.report
          ? { ...state.synthesis, streamText: action.report, streaming: false }
          : state.synthesis,
        timers: { ...state.timers, reviewDoneAt: state.timers.reviewDoneAt || action.timestamp },
      };

    case "REVIEW_ERROR":
      return { ...state, reviewStatus: "error", globalError: action.error };

    case "STREAM_END":
      return {
        ...state,
        reviewStatus: state.reviewStatus === "running" ? "complete" : state.reviewStatus,
        sseUrl: null,
        timers: { ...state.timers, reviewDoneAt: state.timers.reviewDoneAt || action.timestamp },
      };

    default:
      return state;
  }
}

// ── Session persistence ────────────────────────────────────────────────────────

const STORAGE_KEY = "reviewer_state";

function loadPersistedState() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return initialState;
    const saved = JSON.parse(raw);
    // SSE stream is gone after refresh — clear URL and normalise status
    saved.sseUrl = null;
    if (saved.reviewStatus === "running") saved.reviewStatus = "complete";
    // Normalise any agents that were mid-stream
    for (const role of Object.keys(saved.agents || {})) {
      if (saved.agents[role].status === "running") {
        saved.agents[role].status = "done";
        saved.agents[role].streaming = false;
      }
    }
    if (saved.synthesis?.status === "running") {
      saved.synthesis.status = "done";
      saved.synthesis.streaming = false;
    }
    if (!saved.agentOrder) {
      saved.agentOrder = agentOrderForProfile(saved.reviewProfile || "general");
    }
    if (!saved.verificationSummary) saved.verificationSummary = null;
    if (!saved.findings) saved.findings = [];
    if (!saved.consensusFindings) saved.consensusFindings = [];
    if (!saved.disputedFindings) saved.disputedFindings = [];
    if (!saved.convergenceMetrics) saved.convergenceMetrics = null;
    if (!saved.driftSummary) saved.driftSummary = null;
    if (!saved.verdict) saved.verdict = null;
    if (!saved.sessionReports) saved.sessionReports = {};
    if (!saved.finalSummaryMarkdown) saved.finalSummaryMarkdown = "";
    if (!saved.nextStepsMarkdown) saved.nextStepsMarkdown = "";
    if (!saved.artifactSummary) saved.artifactSummary = null;
    // Ensure timers shape exists
    if (!saved.timers) saved.timers = initialState.timers;
    return saved;
  } catch {
    return initialState;
  }
}

function formatCompactNumber(value) {
  if (!value) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

function indexSessionReports(reports = []) {
  return Object.fromEntries((reports || []).map((item) => [item.agent_id, item]));
}

function synthesisStateForProfile(profile, state) {
  if (profile === "llm_repo") {
    return state.synthesis.role === "judge" ? state.synthesis : makeSynthState();
  }
  return state.synthesis.role === "synthesizer" ? state.synthesis : makeSynthState();
}

function baseRoleForAgentKey(agentKey) {
  return agentKey.split("__")[0];
}

function buildLedgerSections(profile, state, reviewerNames) {
  const agentStateFor = (role) => state.agents[role] || makeAgentState();
  const synthesisState = synthesisStateForProfile(profile, state);
  const item = (key, title, subtitle, lensState, metrics, timer) => ({
    key,
    title,
    subtitle,
    state: lensState,
    metrics,
    timer,
  });

  if (profile === "llm_repo") {
    const strictSubtitleByRole = {
      spec_drift: "文件、測試與實作的規格漂移",
      architecture_integrity: "模組邊界、分層與耦合",
      security_boundary: "auth、信任邊界與 secrets",
      runtime_operational: "build、CI、執行期假設與失敗模式",
      test_integrity: "測試覆蓋品質與證據強度",
      llm_artifact_simplification: "generated residue 與多餘複雜度",
      challenger: "質疑證據不足或不夠穩的 findings",
    };
    const strictTitleFor = (agentKey) =>
      agentStateFor(agentKey).displayName || STRICT_AGENT_LABELS[baseRoleForAgentKey(agentKey)] || agentKey;
    const strictItem = (agentKey) =>
      item(
        agentKey,
        strictTitleFor(agentKey),
        strictSubtitleByRole[baseRoleForAgentKey(agentKey)] || "嚴格模式 session",
        agentStateFor(agentKey),
        state.metrics[agentKey],
        state.timers.agents[agentKey]
      );
    const rolesByPrefix = (prefixes) =>
      state.agentOrder.filter((agentKey) => prefixes.includes(baseRoleForAgentKey(agentKey)));

    return [
      {
        title: "規格 / 架構 / 安全",
        items: rolesByPrefix(["spec_drift", "architecture_integrity", "security_boundary"]).map(strictItem),
      },
      {
        title: "執行期 / 測試 / LLM Artifact",
        items: rolesByPrefix(["runtime_operational", "test_integrity", "llm_artifact_simplification"]).map(strictItem),
      },
      {
        title: "挑戰 / 裁決",
        items: [
          ...rolesByPrefix(["challenger"]).map(strictItem),
          item(
            "judge",
            "最終裁決",
            "最終 verdict、收斂與 summary",
            synthesisState,
            state.metrics.judge,
            state.timers.agents.judge
          ),
        ],
      },
    ];
  }

  return [
    {
      title: "規劃",
      items: [
        item(
          "orchestrator",
          "協調規劃",
          "決定檔案範圍、重點與策略",
          state.orchestrator,
          state.metrics.orchestrator,
          state.timers.agents.orchestrator
        ),
      ],
    },
    {
      title: "審查角色",
      items: [
        item(
          "reviewer_1",
          reviewerNames[0] ?? "架構審查",
          "架構視角",
          agentStateFor("reviewer_1"),
          state.metrics.reviewer_1,
          state.timers.agents.reviewer_1
        ),
        item(
          "reviewer_2",
          reviewerNames[1] ?? "後端審查",
          "後端與服務視角",
          agentStateFor("reviewer_2"),
          state.metrics.reviewer_2,
          state.timers.agents.reviewer_2
        ),
        item(
          "reviewer_3",
          reviewerNames[2] ?? "前端與體驗審查",
          "前端、UX 與測試視角",
          agentStateFor("reviewer_3"),
          state.metrics.reviewer_3,
          state.timers.agents.reviewer_3
        ),
      ],
    },
    {
      title: "報告",
      items: [
        item(
          "synthesizer",
          "最終報告整合",
          "整合 review 內容並輸出最終報告",
          synthesisState,
          state.metrics.synthesizer,
          state.timers.agents.synthesizer
        ),
      ],
    },
  ];
}

// ── App shell (inner, has access to theme context) ────────────────────────────

function AppInner() {
  const { theme, toggle } = useTheme();

  const [state, dispatch] = useReducer(reducer, undefined, loadPersistedState);
  const [draftReviewProfile, setDraftReviewProfile] = useState(
    () => loadPersistedState().reviewProfile || "llm_repo"
  );
  const [draftReviewInput, setDraftReviewInput] = useState({
    review_profile: loadPersistedState().reviewProfile || "llm_repo",
    source_mode: "folder",
    ready: false,
  });
  const [reviewEstimate, setReviewEstimate] = useState({
    status: "idle",
    data: null,
    error: null,
  });
  const [models, setModels] = useState([]);
  const [modelConfig, setModelConfig] = useState({
    preset: "balanced",
    overrides: {},
    globalModel: "",
    advancedOpen: false,
  });
  const reviewerNames = REVIEWER_DISPLAY_NAMES;
  const [submitting, setSubmitting] = useState(false);
  const [connectionError, setConnectionError] = useState(null);
  const [appInfo, setAppInfo] = useState({ packaged: false, shutdown_supported: false });
  const [authStatus, setAuthStatus] = useState(null);
  const [authValidating, setAuthValidating] = useState(false);
  const [shutdownStatus, setShutdownStatus] = useState("idle");
  const [shutdownMessage, setShutdownMessage] = useState("");
  const [infoOpen, setInfoOpen] = useState(false);
  const [focusedLensKey, setFocusedLensKey] = useState(null);
  // Track which agents have received at least one agent.stream event this review.
  // Used to prevent agent.message from doubling content when the SDK emits both.
  const streamedAgentsRef = useRef(new Set());

  // Persist state to sessionStorage whenever it changes (skip idle — nothing to save)
  useEffect(() => {
    if (state.reviewStatus !== "idle") {
      try {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      } catch {
        // storage quota exceeded or private mode — silently ignore
      }
    }
  }, [state]);

  useEffect(() => {
    setFocusedLensKey(null);
  }, [state.reviewId]);

  const [byokActive, setByokActive] = useState(false);
  const refreshModels = useCallback(async () => {
    try {
      const data = await fetchModels();
      setModels(data.models || []);
      setByokActive(Boolean(data.byok_active));
      setConnectionError(null);
    } catch (err) {
      setModels([]);
      setConnectionError(err.message);
    }
  }, []);

  // Load available models on mount
  useEffect(() => {
    refreshModels();
  }, [refreshModels]);

  useEffect(() => {
    fetchAppInfo()
      .then((data) => {
        setAppInfo({
          packaged: Boolean(data.packaged),
          shutdown_supported: Boolean(data.shutdown_supported),
          base_url: data.base_url || null,
          port: data.port || null,
        });
      })
      .catch(() => {
        setAppInfo({ packaged: false, shutdown_supported: false });
      });
  }, []);

  useEffect(() => {
    fetchAuthStatus()
      .then((status) => {
        setAuthStatus(status);
      })
      .catch((err) => {
        setConnectionError(err.message);
      });
  }, []);

  useEffect(() => {
    if (!draftReviewInput.ready) {
      setReviewEstimate({ status: "idle", data: null, error: null });
      return;
    }

    const controller = new AbortController();
    const payload = {
      ...reviewModeConfig(draftReviewInput.review_profile || draftReviewProfile),
      source_mode: draftReviewInput.source_mode,
      folder_path: draftReviewInput.folder_path,
      file_paths: draftReviewInput.file_paths,
      uploaded_files: draftReviewInput.uploaded_files,
      focus_prompt: draftReviewInput.focus_prompt,
      model_preset: modelConfig.preset,
      model_overrides:
        Object.keys(modelConfig.overrides).length > 0 ? modelConfig.overrides : undefined,
    };

    setReviewEstimate((prev) => ({
      status: "loading",
      data: prev.status === "ready" ? prev.data : null,
      error: null,
    }));

    const timeoutId = window.setTimeout(async () => {
      try {
        const data = await fetchReviewEstimate(payload, { signal: controller.signal });
        setReviewEstimate({ status: "ready", data, error: null });
      } catch (err) {
        if (err.name === "AbortError") return;
        setReviewEstimate({ status: "error", data: null, error: err.message });
      }
    }, 350);

    return () => {
      controller.abort();
      window.clearTimeout(timeoutId);
    };
  }, [draftReviewInput, draftReviewProfile, modelConfig]);

  // Handle SSE events
  const handleEvent = useCallback((event) => {
    const ts = Date.now();
    switch (event.type) {
      case "agent.started":
        dispatch({
          type: "AGENT_STARTED",
          agent: event.agent,
          model: event.model,
          displayName: event.display_name || event.displayName || null,
          context_window_tokens: resolveContextWindowTokens(event.model, models),
          timestamp: ts,
        });
        break;
      case "agent.stream":
        streamedAgentsRef.current.add(event.agent);
        dispatch({ type: "AGENT_STREAM", agent: event.agent, content: event.content });
        break;
      case "agent.message":
        // Final message — fallback for non-streaming models only.
        // Skip if we already received agent.stream events for this agent to avoid doubling.
        if (!streamedAgentsRef.current.has(event.agent)) {
          dispatch({ type: "AGENT_STREAM", agent: event.agent, content: event.content });
        }
        break;
      case "agent.tool_call":
        dispatch({ type: "AGENT_TOOL_CALL", agent: event.agent, tool_name: event.tool_name, args: event.args });
        break;
      case "agent.done":
        dispatch({ type: "AGENT_DONE", agent: event.agent, timestamp: ts });
        break;
      case "agent.error":
        dispatch({ type: "AGENT_ERROR", agent: event.agent, error: event.error });
        break;
      case "metrics.update":
        dispatch({
          type: "METRICS_UPDATE",
          agent: event.agent,
          input_tokens: event.input_tokens,
          output_tokens: event.output_tokens,
          turns: event.turns,
          quota: event.quota,
          model: event.model,
          context_window_tokens:
            event.context_window_tokens ?? resolveContextWindowTokens(event.model, models),
        });
        break;
      case "orchestrator.plan":
        dispatch({ type: "ORCHESTRATOR_PLAN", plan: event.plan });
        break;
      case "verification.completed":
        dispatch({ type: "VERIFICATION_UPDATED", summary: event.verification_summary || null });
        break;
      case "finding.emitted":
        dispatch({ type: "FINDING_EMITTED", finding: event.finding });
        break;
      case "judge.summary":
        dispatch({ type: "JUDGE_SUMMARY", summary: event.summary || {} });
        break;
      case "review.verdict":
        dispatch({ type: "REVIEW_VERDICT", verdict: event.verdict });
        break;
      case "review.complete":
        dispatch({
          type: "REVIEW_COMPLETE",
          timestamp: ts,
          report: event.report,
          verdict: event.verdict,
          findings: event.findings,
          consensusFindings: event.consensus_findings,
          disputedFindings: event.disputed_findings,
          convergenceMetrics: event.convergence_metrics,
          verificationSummary: event.verification_summary,
          driftSummary: event.drift_summary,
          sessionReports: indexSessionReports(event.session_reports || []),
          finalSummaryMarkdown: event.final_summary_markdown || "",
          nextStepsMarkdown: event.next_steps_markdown || "",
          artifactSummary: event.artifact_summary || null,
        });
        break;
      case "review.error":
        dispatch({ type: "REVIEW_ERROR", error: event.error });
        break;
      case "stream.end":
        dispatch({ type: "STREAM_END", timestamp: ts });
        break;
    }
  }, [models]);

  const { connected, error: sseError } = useSSE(state.sseUrl, handleEvent);

  async function handleSubmit(formData) {
    setSubmitting(true);
    setConnectionError(null);
    try {
      const payload = {
        ...reviewModeConfig(formData.review_profile || draftReviewProfile),
        ...formData,
        model_preset: modelConfig.preset,
        model_overrides: Object.keys(modelConfig.overrides).length > 0
          ? modelConfig.overrides
          : undefined,
      };
      const { review_id, sse_url } = await startReview(payload);
      streamedAgentsRef.current = new Set();
      dispatch({
        type: "REVIEW_STARTED",
        reviewId: review_id,
        sseUrl: sse_url,
        reviewProfile: payload.review_profile || "general",
        timestamp: Date.now(),
      });
    } catch (err) {
      setConnectionError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleValidateAccount() {
    setAuthValidating(true);
    try {
      const status = await validateAuth();
      setAuthStatus(status);
      if (status.ready) {
        await refreshModels();
      }
    } catch (err) {
      setConnectionError(err.message);
    } finally {
      setAuthValidating(false);
    }
  }

  async function handleShutdown() {
    setShutdownStatus("pending");
    setShutdownMessage("");
    try {
      const response = await shutdownApp();
      setShutdownStatus("done");
      setShutdownMessage(
        response.detail ||
          "後端正在關閉，瀏覽器分頁不會自動關閉，請手動關閉此頁面。"
      );
    } catch (err) {
      setShutdownStatus("error");
      setShutdownMessage(err.message);
    }
  }

  const isRunning = state.reviewStatus === "running";
  const activeLayoutProfile =
    state.reviewStatus === "idle" ? draftReviewProfile : state.reviewProfile;
  const ledgerSections = buildLedgerSections(activeLayoutProfile, state, reviewerNames);
  const focusedLens =
    ledgerSections.flatMap((section) => section.items).find((item) => item.key === focusedLensKey) ||
    null;
  const focusedSessionReport = focusedLens ? state.sessionReports[focusedLens.key] || null : null;
  const reportRoleLabel =
    activeLayoutProfile === "llm_repo" ? "嚴格判定" : "最終報告";
  const reportLensState = synthesisStateForProfile(activeLayoutProfile, state);
  const reportMetricsKey = activeLayoutProfile === "llm_repo" ? "judge" : "synthesizer";
  const workspaceMode = focusedLens ? "role" : "final";
  const workspaceRoleLabel = focusedLens ? focusedLens.title : reportRoleLabel;
  const workspaceSubtitle = focusedLens ? focusedLens.subtitle : "";
  const workspaceState = focusedLens ? focusedLens.state : reportLensState;
  const workspaceTimer = focusedLens ? focusedLens.timer : state.timers.agents[reportMetricsKey];
  const workspaceMetrics = focusedLens ? focusedLens.metrics : state.metrics[reportMetricsKey];
  const workspaceContent =
    focusedSessionReport?.report_markdown || workspaceState?.streamText || "";
  const workspacePlan = focusedLens?.key === "orchestrator" ? state.orchestrator.plan : null;

  // Warn before refresh/close when a review is loaded
  useEffect(() => {
    if (state.reviewStatus === "idle") return;
    function onBeforeUnload(e) {
      sessionStorage.removeItem(STORAGE_KEY);
      e.preventDefault();
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [state.reviewStatus]);

  return (
    <div className="flex min-h-screen flex-col">
      <Masthead
        theme={theme}
        onToggleTheme={toggle}
        infoOpen={infoOpen}
        onToggleInfo={() => setInfoOpen((open) => !open)}
        appInfo={appInfo}
        shutdownStatus={shutdownStatus}
        onShutdown={handleShutdown}
      />

      <StatusRibbon
        metrics={state.metrics}
        reviewStatus={state.reviewStatus}
        models={models}
        byokActive={byokActive}
        startedAt={state.timers.reviewStartedAt}
        doneAt={state.timers.reviewDoneAt}
        connected={connected}
        sseError={sseError}
        modelPreset={modelConfig.preset}
      />

      <main className="grid min-h-0 flex-1 gap-4 px-4 pb-6 pt-4 sm:px-6 lg:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[340px_minmax(0,1fr)_360px]">
        <div className="order-2 min-h-0 lg:order-1">
          <CommandRail>
            <AuthStatusCard
              status={authStatus}
              validating={authValidating}
              disabled={isRunning || submitting}
              onValidate={handleValidateAccount}
            />
            <TaskInput
              onSubmit={handleSubmit}
              disabled={isRunning || submitting}
              packaged={appInfo.packaged}
              reviewProfile={draftReviewProfile}
              onReviewProfileChange={setDraftReviewProfile}
              onDraftChange={setDraftReviewInput}
              modelConfig={modelConfig}
              onModelConfigChange={setModelConfig}
              models={models}
            />
            <ModelRouterPanel
              config={modelConfig}
              onChange={setModelConfig}
              models={models}
              disabled={isRunning || submitting}
              reviewerNames={reviewerNames}
              reviewProfile={draftReviewProfile}
              estimate={reviewEstimate.data}
              estimateStatus={reviewEstimate.status}
              estimateError={reviewEstimate.error}
              byokActive={byokActive}
            />

            {connectionError ? (
              <div className="paper-panel text-sm leading-7 text-[var(--danger)]">
                {connectionError}
              </div>
            ) : null}

            {state.globalError ? (
              <div className="paper-panel text-sm leading-7 text-[var(--danger)]">
                review 失敗：{state.globalError}
              </div>
            ) : null}

            {shutdownMessage ? (
              <div
                className={`paper-panel text-sm leading-7 ${
                  shutdownStatus === "error" ? "text-[var(--danger)]" : "text-[var(--warning)]"
                }`}
              >
                {shutdownMessage}
              </div>
            ) : null}
          </CommandRail>
        </div>

        <div className="order-1 min-h-0 lg:order-2">
          <ReportWorkspace
            reviewStatus={state.reviewStatus}
            reviewProfile={activeLayoutProfile}
            roleLabel={workspaceRoleLabel}
            focusMode={workspaceMode}
            focusSubtitle={workspaceSubtitle}
            draftReviewProfile={draftReviewProfile}
            reportContent={workspaceContent}
          >
            <SynthesisPanel
              state={workspaceState}
              timer={workspaceTimer}
              metrics={workspaceMetrics}
              roleLabel={workspaceRoleLabel}
              contentMode={workspaceMode}
              subtitle={workspaceSubtitle}
              verdict={state.verdict}
              verificationSummary={state.verificationSummary}
              consensusFindings={state.consensusFindings}
              disputedFindings={state.disputedFindings}
              convergenceMetrics={state.convergenceMetrics}
              driftSummary={state.driftSummary}
              sessionReports={Object.values(state.sessionReports)}
              sessionReport={focusedSessionReport}
              finalSummaryMarkdown={state.finalSummaryMarkdown}
              nextStepsMarkdown={state.nextStepsMarkdown}
              artifactSummary={state.artifactSummary}
              onResetFocus={workspaceMode === "role" ? () => setFocusedLensKey(null) : null}
              plan={workspacePlan}
            />
          </ReportWorkspace>
        </div>

        <div className="order-3 min-h-0 lg:col-span-2 xl:col-span-1">
          <RunLedger
            sections={ledgerSections}
            expandedKey={focusedLensKey}
            onOpen={(key) => setFocusedLensKey((current) => (current === key ? null : key))}
          />
        </div>
      </main>
    </div>
  );
}

// ── Root export — wraps with ThemeProvider ────────────────────────────────────

export default function App() {
  return (
    <ThemeProvider>
      <AppInner />
    </ThemeProvider>
  );
}
