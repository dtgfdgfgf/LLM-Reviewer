import { ElapsedTime } from "./ElapsedTime.jsx";

const PREMIUM_REQUEST_COST_USD = 0.04;

function fmtTokens(n) {
  if (!n) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function fmtPremiumReqs(n) {
  if (n === Math.floor(n)) return String(n);
  return n.toFixed(1);
}

function billingMultiplier(modelId, models) {
  if (!modelId || !models.length) return 1;
  const found = models.find((model) => model.id === modelId);
  return found?.billing_multiplier ?? 1;
}

function hasBillableSession(agentMetrics) {
  return Boolean(agentMetrics?.model) && (agentMetrics?.turns || 0) > 0;
}

function agentPremiumRequests(agentMetrics, models) {
  if (!hasBillableSession(agentMetrics)) return 0;
  return billingMultiplier(agentMetrics.model, models);
}

function statusLabel(reviewStatus) {
  const map = {
    idle: "待命",
    running: "進行中",
    complete: "完成",
    error: "錯誤",
  };
  return map[reviewStatus] || reviewStatus;
}

export function StatusRibbon({
  metrics,
  reviewStatus,
  models,
  byokActive,
  startedAt,
  doneAt,
  connected,
  sseError,
  modelPreset,
}) {
  const totals = Object.values(metrics).reduce(
    (acc, item) => ({
      input: acc.input + (item.input_tokens || 0),
      output: acc.output + (item.output_tokens || 0),
    }),
    { input: 0, output: 0 }
  );

  const totalTokens = totals.input + totals.output;
  const totalPremiumReqs = !byokActive
    ? Object.values(metrics).reduce((sum, item) => sum + agentPremiumRequests(item, models), 0)
    : 0;
  const totalBillableSessions = !byokActive
    ? Object.values(metrics).reduce((sum, item) => sum + (hasBillableSession(item) ? 1 : 0), 0)
    : 0;
  const quota = Object.values(metrics).find((item) => item.quota?.entitlement_requests)?.quota;

  return (
    <section className="paper-panel-strong mx-4 mt-4 px-5 py-3 sm:mx-6">
      <div className="flex flex-wrap items-center gap-2">
        <span className="meta-pill">
          <span className={`status-dot ${reviewStatus}`} />
          {statusLabel(reviewStatus)}
        </span>
        <span className="meta-pill">Preset：{modelPreset}</span>
        {startedAt ? (
          <span className="meta-pill">
            已耗時 <ElapsedTime startedAt={startedAt} doneAt={doneAt} />
          </span>
        ) : null}
        <span className="meta-pill">{fmtTokens(totalTokens)} tokens</span>
        {connected ? <span className="meta-pill">SSE 已連線</span> : null}
        {sseError ? <span className="meta-pill text-[var(--danger)]">SSE：{sseError}</span> : null}
        {!byokActive && totalPremiumReqs > 0 ? (
          <>
            <span className="meta-pill">{totalBillableSessions} sessions</span>
            <span className="meta-pill">{fmtPremiumReqs(totalPremiumReqs)} premium reqs</span>
            <span className="meta-pill">約 ${(totalPremiumReqs * PREMIUM_REQUEST_COST_USD).toFixed(2)}</span>
          </>
        ) : null}
        {byokActive ? <span className="meta-pill">BYOK 計價</span> : null}
        {quota ? (
          <span className="meta-pill">
            Premium quota {quota.used_requests ?? "?"}/{quota.entitlement_requests ?? "?"}
          </span>
        ) : null}
      </div>
    </section>
  );
}
