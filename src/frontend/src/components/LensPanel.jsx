import { ElapsedTime } from "./ElapsedTime.jsx";

const ROLE_ACCENTS = {
  orchestrator: "var(--accent)",
  reviewer_1: "#8d4632",
  reviewer_2: "#a36a1f",
  reviewer_3: "#2f6b5c",
  synthesizer: "#8f4f2f",
  spec_drift: "#9c4e3b",
  architecture_integrity: "#8d4632",
  security_boundary: "#9c3b37",
  runtime_operational: "#416e8b",
  test_integrity: "#2f6b5c",
  llm_artifact_simplification: "#a36a1f",
  challenger: "#7a5b34",
  judge: "#8f4f2f",
};

function fmtTokens(n) {
  if (!n) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function previewText(text) {
  return text
    ?.split("\n")
    .map((line) => line.trim())
    .find(Boolean);
}

export function LensPanel({
  lensKey,
  title,
  subtitle,
  state,
  metrics,
  timer,
  selected,
  onOpen,
}) {
  const status = state?.status || "idle";
  const model = state?.model;
  const accent = ROLE_ACCENTS[lensKey] || "var(--accent)";
  const totalTokens = (metrics?.input_tokens || 0) + (metrics?.output_tokens || 0);
  const summary = previewText(state?.streamText);
  const tools = state?.toolCalls?.length || 0;
  const statusLabel = {
    idle: "待命",
    running: "進行中",
    done: "完成",
    error: "錯誤",
  }[status] || status;

  return (
    <button
      type="button"
      onClick={onOpen}
      className={`paper-panel text-left transition-transform duration-150 hover:-translate-y-[1px] ${
        selected ? "ring-1 ring-[var(--accent)]" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{
                backgroundColor:
                  status === "running"
                    ? accent
                    : status === "done"
                      ? "var(--success)"
                      : status === "error"
                        ? "var(--danger)"
                        : "var(--line)",
              }}
            />
            <span className="font-display text-lg text-[var(--ink)]">{title}</span>
          </div>
          {subtitle ? (
            <p className="mt-1 font-mono text-[11px] text-[var(--muted)]">{subtitle}</p>
          ) : null}
        </div>
        <span className="meta-pill">{statusLabel}</span>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <span className="meta-pill strong">模型: {model || "模型待定"}</span>
        <span className="meta-pill">{fmtTokens(totalTokens)} tokens</span>
        <span className="meta-pill">{tools} 個工具</span>
        {timer?.startedAt ? (
          <span className="meta-pill">
            <ElapsedTime startedAt={timer.startedAt} doneAt={timer.doneAt} />
          </span>
        ) : null}
      </div>

      <p className="mt-4 max-h-[4.8rem] overflow-hidden text-sm leading-6 text-[var(--muted)]">
        {summary ||
          (status === "idle"
            ? "等待這個角色開始執行。"
            : status === "error"
              ? state?.error || "這個角色回報了錯誤。"
              : "角色一有輸出，就會先在這裡顯示摘要。")}
      </p>
    </button>
  );
}
