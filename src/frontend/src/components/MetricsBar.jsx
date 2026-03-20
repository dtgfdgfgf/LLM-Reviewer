import { useThemeClasses } from "../ThemeContext.jsx";

const PREMIUM_REQUEST_COST_USD = 0.04;

/**
 * MetricsBar — real-time token/context/quota display.
 *
 * Cost model:
 *  - Copilot session-based reviews: one billable session × model billing multiplier
 *  - BYOK: no dollar cost shown — user calculates from token counts + vendor pricing
 */
export function MetricsBar({ metrics, reviewStatus, reviewerNames = [], models = [], byokActive = false }) {
  const { d } = useThemeClasses();

  const totals = Object.values(metrics).reduce(
    (acc, m) => ({
      input: acc.input + (m.input_tokens || 0),
      output: acc.output + (m.output_tokens || 0),
    }),
    { input: 0, output: 0 }
  );

  // Calculate aggregate premium request cost (Copilot mode only)
  const totalPremiumReqs = !byokActive
    ? Object.values(metrics).reduce(
      (sum, m) => sum + agentPremiumRequests(m, models),
      0
    )
    : 0;
  const totalBillableSessions = !byokActive
    ? Object.values(metrics).reduce((sum, m) => sum + (hasBillableSession(m) ? 1 : 0), 0)
    : 0;
  const totalCost = totalPremiumReqs * PREMIUM_REQUEST_COST_USD;

  // Get quota from any agent that has it
  const quota = Object.values(metrics).find((m) => m.quota?.entitlement_requests)?.quota;

  const totalTokens = totals.input + totals.output;

  return (
    <div className={`flex flex-wrap items-center gap-x-6 gap-y-1.5 px-4 py-2 border-b text-xs font-mono ${d("bg-gray-900 border-gray-800", "bg-white border-slate-200")
      }`}>
      {/* Status indicator */}
      <div className="flex items-center gap-2">
        <span
          className={`h-2 w-2 rounded-full ${reviewStatus === "running"
            ? "bg-emerald-500 animate-pulse"
            : reviewStatus === "complete"
              ? "bg-blue-500"
              : reviewStatus === "error"
                ? "bg-red-500"
                : d("bg-gray-600", "bg-slate-300")
            }`}
        />
        <span className={`uppercase tracking-wider text-[10px] ${d("text-gray-300", "text-slate-600")}`}>
          {reviewStatus || "idle"}
        </span>
      </div>

      <div className={`h-4 w-px ${d("bg-gray-700", "bg-slate-200")}`} />

      {/* Token counts */}
      <MetricItem label="IN" value={fmtTokens(totals.input)} color={d("text-sky-400", "text-sky-600")} />
      <MetricItem label="OUT" value={fmtTokens(totals.output)} color={d("text-violet-400", "text-violet-600")} />
      <MetricItem label="TOTAL" value={fmtTokens(totalTokens)} color={d("text-gray-100", "text-gray-700")} />

      {/* Cost display — Copilot: premium reqs × $0.04; BYOK: vendor-pricing note */}
      {!byokActive && totalPremiumReqs > 0 && (
        <>
          <div className={`h-4 w-px ${d("bg-gray-700", "bg-slate-200")}`} />
          <MetricItem
            label="SESSIONS"
            value={String(totalBillableSessions)}
            color={d("text-amber-300", "text-amber-700")}
          />
          <MetricItem
            label="PREMIUM"
            value={fmtPremiumReqs(totalPremiumReqs)}
            color={d("text-emerald-400", "text-emerald-600")}
          />
          <MetricItem
            label="EST. COST"
            value={`$${totalCost.toFixed(2)}`}
            color={d("text-emerald-400", "text-emerald-600")}
          />
          <span className={`text-[9px] ${d("text-gray-400", "text-slate-400")}`}>
            Official rule: billable sessions × model multiplier
          </span>
        </>
      )}
      {byokActive && totalTokens > 0 && (
        <>
          <div className={`h-4 w-px ${d("bg-gray-700", "bg-slate-200")}`} />
          <span className={`text-[10px] ${d("text-gray-400", "text-slate-400")}`}>
            Cost: see vendor pricing for token usage
          </span>
        </>
      )}

      {quota && (
        <>
          <div className={`h-4 w-px ${d("bg-gray-700", "bg-slate-200")}`} />
          {/* Premium request quota */}
          <div className="flex items-center gap-2">
            <span className={d("text-gray-300", "text-slate-600")}>PREMIUM</span>
            {quota.is_unlimited ? (
              <span className="text-emerald-500">∞ unlimited</span>
            ) : (
              <>
                <div className={`w-16 h-1.5 rounded-full overflow-hidden ${d("bg-gray-700", "bg-slate-200")}`}>
                  <div
                    className={`h-full rounded-full transition-all ${quota.remaining_percentage > 50
                      ? "bg-emerald-500"
                      : quota.remaining_percentage > 20
                        ? "bg-amber-500"
                        : "bg-red-500"
                      }`}
                    style={{ width: `${Math.max(0, quota.remaining_percentage)}%` }}
                  />
                </div>
                <span className={d("text-gray-100", "text-gray-700")}>
                  {quota.used_requests ?? "?"} / {quota.entitlement_requests ?? "?"}
                  <span className={`ml-1 ${d("text-gray-300", "text-slate-500")}`}>
                    ({quota.remaining_percentage?.toFixed(0)}% left)
                  </span>
                </span>
              </>
            )}
          </div>
        </>
      )}

      {/* Per-agent breakdown */}
      {Object.keys(metrics).length > 0 && (
        <>
          <div className={`h-4 w-px ${d("bg-gray-700", "bg-slate-200")}`} />
          <div className="flex items-center gap-3 flex-wrap">
            {orderedAgentEntries(metrics).map(([agent, m]) => (
              <AgentMetric
                key={agent}
                agent={agent}
                inputTokens={m.input_tokens || 0}
                outputTokens={m.output_tokens || 0}
                turns={m.turns || 0}
                billableSession={hasBillableSession(m)}
                premiumReqs={!byokActive ? agentPremiumRequests(m, models) : 0}
                byokActive={byokActive}
                reviewerNames={reviewerNames}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function MetricItem({ label, value, color }) {
  const { d } = useThemeClasses();
  return (
    <div className="flex items-center gap-1.5">
      <span className={`text-[10px] uppercase tracking-wider ${d("text-gray-300", "text-slate-500")}`}>{label}</span>
      <span className={`font-mono ${color}`}>{value}</span>
    </div>
  );
}

function orderedAgentEntries(metrics) {
  const priority = {
    orchestrator: 0,
    reviewer_1: 10,
    reviewer_2: 11,
    reviewer_3: 12,
    spec_drift: 20,
    architecture_integrity: 21,
    security_boundary: 22,
    runtime_operational: 23,
    test_integrity: 24,
    llm_artifact_simplification: 25,
    challenger: 26,
    synthesizer: 30,
    judge: 31,
  };
  return Object.entries(metrics).sort(
    ([a], [b]) => (priority[a] ?? 100) - (priority[b] ?? 100) || a.localeCompare(b)
  );
}

/**
 * Compute premium requests consumed by one agent.
 * Official rule for this UI: one billable session costs billing_multiplier premium requests.
 */
function agentPremiumRequests(agentMetrics, models) {
  if (!hasBillableSession(agentMetrics)) return 0;
  const multiplier = billingMultiplier(agentMetrics.model, models);
  return multiplier;
}

function billingMultiplier(modelId, models) {
  if (!modelId || !models.length) return 1;
  const found = models.find((m) => m.id === modelId);
  return found?.billing_multiplier ?? 1;
}

function hasBillableSession(agentMetrics) {
  return Boolean(agentMetrics?.model) && (agentMetrics?.turns || 0) > 0;
}

function AgentMetric({ agent, inputTokens, outputTokens, turns, billableSession, premiumReqs, byokActive, reviewerNames }) {
  const { d } = useThemeClasses();
  const colors = {
    reviewer_1: d("text-red-400", "text-red-600"),
    reviewer_2: d("text-amber-400", "text-amber-600"),
    reviewer_3: d("text-emerald-400", "text-emerald-600"),
    synthesizer: d("text-violet-400", "text-violet-600"),
    judge: d("text-violet-400", "text-violet-600"),
    orchestrator: d("text-indigo-400", "text-indigo-600"),
    spec_drift: d("text-rose-400", "text-rose-600"),
    architecture_integrity: d("text-orange-400", "text-orange-600"),
    security_boundary: d("text-red-400", "text-red-600"),
    runtime_operational: d("text-sky-400", "text-sky-600"),
    test_integrity: d("text-emerald-400", "text-emerald-600"),
    llm_artifact_simplification: d("text-amber-400", "text-amber-600"),
    challenger: d("text-fuchsia-400", "text-fuchsia-600"),
  };
  const label = agentLabel(agent, reviewerNames);
  const cost = premiumReqs * PREMIUM_REQUEST_COST_USD;
  return (
    <span className={`flex items-center gap-1 ${colors[agent] || d("text-gray-400", "text-slate-500")} text-[10px]`}>
      <span className="font-semibold">{label}</span>
      <span className={d("text-gray-300", "text-slate-500")}>
        {fmtTokens(inputTokens)}↑ {fmtTokens(outputTokens)}↓
      </span>
      <span className={d("text-gray-400", "text-slate-500")}>
        {turns} turns
      </span>
      {!byokActive && cost > 0 && (
        <span className={d("text-gray-300", "text-slate-500")}>
          {billableSession ? `${fmtPremiumReqs(premiumReqs)} PR` : "0 PR"}
        </span>
      )}
      {!byokActive && cost > 0 && (
        <span className={d("text-gray-300", "text-slate-500")}>${cost.toFixed(2)}</span>
      )}
    </span>
  );
}

function agentLabel(agent, reviewerNames) {
  const labels = {
    reviewer_1: reviewerNames[0] ?? "Architecture",
    reviewer_2: reviewerNames[1] ?? "Backend",
    reviewer_3: reviewerNames[2] ?? "Frontend",
    synthesizer: "Synth",
    orchestrator: "Orch",
    judge: "Judge",
    spec_drift: "Spec",
    architecture_integrity: "Arch",
    security_boundary: "Sec",
    runtime_operational: "Run",
    test_integrity: "Test",
    llm_artifact_simplification: "LLM",
    challenger: "Challenge",
  };
  return labels[agent] ?? agent;
}

function fmtTokens(n) {
  if (n === 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function fmtPremiumReqs(n) {
  if (n === Math.floor(n)) return String(n);
  return n.toFixed(1);
}
