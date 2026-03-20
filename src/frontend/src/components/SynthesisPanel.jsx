import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import { CopyButton } from "./AgentPanel.jsx";
import { ElapsedTime } from "./ElapsedTime.jsx";
import { downloadJson, downloadMarkdown } from "../utils/download.js";

function timestampFragment() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function downloadReport(prefix, text) {
  downloadMarkdown(`${prefix}-${timestampFragment()}.md`, text);
}

function verificationStatusLabel(check) {
  if (check?.kind_hint === "coverage_gap") return "部分覆蓋";
  if (check?.kind_hint === "label_mismatch" && check?.status === "failed") return "標籤失真";
  if (check?.kind_hint === "env_gap" && check?.status === "skipped") return "未偵測";
  return {
    passed: "通過",
    failed: "失敗",
    unavailable: "不可用",
    skipped: "未配置 / 不適用",
  }[check?.status] || check?.status;
}

function verificationTitle(check) {
  const base = check?.display_name || check?.name || "unknown";
  return check?.scope && check.scope !== "repo-wide" ? `${base} [${check.scope}]` : base;
}

function verificationMeta(check) {
  const bits = [check?.role, check?.applicability];
  if (check?.working_dir && check.working_dir !== ".") bits.push(check.working_dir);
  return bits.filter(Boolean).join(" / ");
}

function SummaryPill({ label, value, tone = "neutral" }) {
  const tones = {
    neutral: "border-[var(--line)] bg-[var(--surface)] text-[var(--ink)]",
    success: "border-[rgba(47,107,92,0.25)] bg-[rgba(47,107,92,0.08)] text-[var(--success)]",
    warning: "border-[rgba(163,106,31,0.25)] bg-[rgba(163,106,31,0.08)] text-[var(--warning)]",
    danger: "border-[rgba(181,82,51,0.25)] bg-[rgba(181,82,51,0.08)] text-[var(--danger)]",
  };

  return (
    <span
      className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] ${tones[tone]}`}
    >
      {label}: {value}
    </span>
  );
}

function DisclosurePanel({ title, badge = null, defaultOpen = false, children }) {
  if (!children) return null;

  return (
    <details className="disclosure-panel" open={defaultOpen}>
      <summary className="disclosure-summary">
        <div>
          <p className="paper-kicker">{title}</p>
        </div>
        {badge ? <span className="meta-pill">{badge}</span> : null}
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}

function VerificationSnapshot({
  verdict,
  verificationSummary,
  consensusFindings,
  disputedFindings,
  convergenceMetrics,
  driftSummary,
  defaultOpen,
  title,
}) {
  const summaryTone =
    verdict === "PASS" ? "success" : verdict === "FAIL" ? "danger" : "warning";
  const hasContent =
    Boolean(verdict) ||
    consensusFindings.length > 0 ||
    disputedFindings.length > 0 ||
    Boolean(driftSummary?.summary) ||
    Boolean(convergenceMetrics) ||
    (verificationSummary?.checks?.length || 0) > 0;

  if (!hasContent) return null;

  return (
    <DisclosurePanel
      title={title}
      badge={verdict ? `判定 ${verdict}` : "整體狀態"}
      defaultOpen={defaultOpen}
    >
      <div className="flex flex-wrap gap-2">
        {verdict ? <SummaryPill label="判定" value={verdict} tone={summaryTone} /> : null}
        {verificationSummary ? (
          <SummaryPill label="驗證" value={verificationSummary.checks?.length || 0} />
        ) : null}
        <SummaryPill label="共識" value={consensusFindings.length} tone="success" />
        <SummaryPill label="爭議" value={disputedFindings.length} tone="warning" />
      </div>

      {(driftSummary?.summary || convergenceMetrics || verificationSummary?.checks?.length > 0) ? (
        <div className="editor-grid mt-6">
          <section className="paper-panel">
            <p className="paper-kicker">驗證結果</p>
            {verificationSummary?.checks?.length > 0 ? (
              <div className="mt-3 space-y-2">
                {verificationSummary.checks.slice(0, 6).map((check) => (
                  <div
                    key={`${check.name}-${check.scope}-${check.working_dir || "."}`}
                    className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3 text-sm leading-7 text-[var(--muted)]"
                  >
                    <span className="font-semibold text-[var(--ink)]">{verificationTitle(check)}</span>:{" "}
                    {verificationStatusLabel(check)}
                    {" — "}
                    {check.summary}
                    {verificationMeta(check) ? (
                      <div className="text-xs uppercase tracking-[0.16em] text-[var(--muted)]/80">
                        {verificationMeta(check)}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-sm leading-7 text-[var(--muted)]">
                嚴格模式的 verification 結果會顯示在這裡。
              </p>
            )}
          </section>

          <section className="paper-panel">
            <p className="paper-kicker">收斂情況</p>
            {driftSummary?.summary ? (
              <p className="mt-3 text-sm leading-7 text-[var(--muted)]">{driftSummary.summary}</p>
            ) : null}
            {convergenceMetrics ? (
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {[
                  ["一致", convergenceMetrics.agreement_count ?? 0],
                  ["分歧", convergenceMetrics.disagreement_count ?? 0],
                  ["證據密度", convergenceMetrics.evidence_density ?? 0],
                  ["未解決爭議", convergenceMetrics.unresolved_dispute_count ?? 0],
                ].map(([label, value]) => (
                  <div
                    key={label}
                    className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3"
                  >
                    <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[var(--muted)]">
                      {label}
                    </div>
                    <div className="mt-2 text-xl font-semibold text-[var(--ink)]">{value}</div>
                  </div>
                ))}
              </div>
            ) : null}
            {convergenceMetrics ? (
              <p className="mt-4 text-xs leading-6 text-[var(--muted)]">
                證據密度代表每個 finding 平均引用的證據數，數值越高表示每項結論附帶的證據引用越多。
              </p>
            ) : null}
          </section>
        </div>
      ) : null}
    </DisclosurePanel>
  );
}

export function SynthesisPanel({
  state,
  timer,
  metrics,
  roleLabel = "最終報告",
  contentMode = "final",
  subtitle = "",
  verdict = null,
  verificationSummary = null,
  consensusFindings = [],
  disputedFindings = [],
  convergenceMetrics = null,
  driftSummary = null,
  sessionReports = [],
  sessionReport = null,
  finalSummaryMarkdown = "",
  nextStepsMarkdown = "",
  artifactSummary = null,
  onResetFocus = null,
  plan = null,
}) {
  const bottomRef = useRef(null);
  const isRoleFocus = contentMode === "role";
  const hasSessionReport = Boolean(sessionReport?.report_markdown);
  const documentText = isRoleFocus
    ? sessionReport?.report_markdown || state.streamText || ""
    : state.streamText || "";
  const totalTokens = (metrics?.input_tokens || 0) + (metrics?.output_tokens || 0);
  const copyLabel = isRoleFocus
    ? hasSessionReport
      ? "複製 Session 報告"
      : "複製即時輸出"
    : "複製最終文件";
  const exportPayloadAvailable =
    consensusFindings.length > 0 ||
    disputedFindings.length > 0 ||
    verificationSummary ||
    finalSummaryMarkdown ||
    nextStepsMarkdown ||
    artifactSummary ||
    sessionReports.length > 0 ||
    verdict;
  const contentKicker = isRoleFocus
    ? hasSessionReport
      ? "Session 報告"
      : state.streaming
        ? "即時輸出"
        : "角色輸出"
    : state.streaming
      ? "報告草稿"
      : "最終文件";

  useEffect(() => {
    if (state.streaming) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [state.streamText, state.streaming]);

  if (!isRoleFocus && state.status === "idle" && !state.streamText) {
    return (
      <div className="paper-panel-strong min-h-[280px] sm:min-h-[520px]">
        <p className="paper-kicker">報告區</p>
        <h3 className="mt-3 font-display text-3xl text-[var(--ink)]">等待第一份草稿</h3>
        <p className="mt-4 max-w-2xl text-sm leading-7 text-[var(--muted)]">
          等角色開始完成自己的工作後，這裡會逐步組成可閱讀的最終報告，包含 verdict、
          驗證狀態，以及可下載的結果檔。
        </p>
      </div>
    );
  }

  return (
    <article className="paper-panel-strong min-h-[280px] sm:min-h-[520px]">
      <div className="flex flex-col gap-6 border-b border-[var(--line)] pb-5 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="paper-kicker">{contentKicker}</p>
          <h3 className="mt-2 font-display text-4xl leading-none text-[var(--ink)]">
            {roleLabel}
          </h3>
          {subtitle ? (
            <p className="mt-3 max-w-2xl text-sm leading-7 text-[var(--muted)]">{subtitle}</p>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <span className="meta-pill strong">模型: {state.model || "模型待定"}</span>
            {timer?.startedAt ? (
              <span className="meta-pill">
                <ElapsedTime startedAt={timer.startedAt} doneAt={timer.doneAt} />
              </span>
            ) : null}
            <span className="meta-pill">{totalTokens.toLocaleString()} tokens</span>
            <span className="meta-pill">{state.toolCalls?.length || 0} 個工具</span>
          </div>
        </div>

        <div className="flex flex-wrap gap-2 lg:justify-end">
          {isRoleFocus && onResetFocus ? (
            <button type="button" className="ghost-button" onClick={onResetFocus}>
              回到最終文件
            </button>
          ) : null}
          {documentText ? <CopyButton text={documentText} label={copyLabel} /> : null}
          {!isRoleFocus && documentText ? (
            <button
              type="button"
              className="subtle-button"
              onClick={() => downloadReport("reviewer-report", documentText)}
            >
              下載目前內容
            </button>
          ) : null}
          {isRoleFocus && hasSessionReport ? (
            <button
              type="button"
              className="subtle-button"
              onClick={() =>
                downloadReport(
                  `reviewer-session-${sessionReport.agent_id || "role"}`,
                  sessionReport.report_markdown
                )
              }
            >
              下載此 Session 報告
            </button>
          ) : null}
          {!isRoleFocus && exportPayloadAvailable ? (
            <details className="action-menu">
              <summary className="subtle-button">更多匯出</summary>
              <div className="action-menu-panel">
                <button
                  type="button"
                  className="subtle-button w-full justify-between"
                  onClick={() =>
                    downloadJson("findings.json", {
                      consensus_findings: consensusFindings,
                      disputed_findings: disputedFindings,
                    })
                  }
                >
                  下載 findings JSON
                </button>
                <button
                  type="button"
                  className="subtle-button w-full justify-between"
                  onClick={() =>
                    downloadJson("review_summary.json", {
                      verdict,
                      verification_summary: verificationSummary,
                      consensus_findings: consensusFindings,
                      disputed_findings: disputedFindings,
                      convergence_metrics: convergenceMetrics,
                      drift_summary: driftSummary,
                      session_reports: sessionReports,
                      final_summary_markdown: finalSummaryMarkdown,
                      next_steps_markdown: nextStepsMarkdown,
                      artifact_summary: artifactSummary,
                    })
                  }
                >
                  下載 summary JSON
                </button>
                {finalSummaryMarkdown ? (
                  <button
                    type="button"
                    className="subtle-button w-full justify-between"
                    onClick={() => downloadReport("reviewer-final-summary", finalSummaryMarkdown)}
                  >
                    下載最終統整
                  </button>
                ) : null}
                {nextStepsMarkdown ? (
                  <button
                    type="button"
                    className="subtle-button w-full justify-between"
                    onClick={() => downloadReport("reviewer-next-steps", nextStepsMarkdown)}
                  >
                    下載下一步建議
                  </button>
                ) : null}
              </div>
            </details>
          ) : null}
        </div>
      </div>

      <div className="editor-divider mt-6 pt-6">
        {state.error ? (
          <div className="mb-4 rounded-[24px] border border-[rgba(181,82,51,0.25)] bg-[rgba(181,82,51,0.08)] px-4 py-4 text-sm leading-7 text-[var(--danger)]">
            {state.error}
          </div>
        ) : null}
        {state.streaming || (isRoleFocus && !hasSessionReport && documentText) ? (
          <pre
            className={`stream-text text-sm leading-7 text-[var(--ink)] ${state.streaming ? "cursor-blink" : ""}`}
          >
            {documentText}
          </pre>
        ) : documentText ? (
          <div className="synthesis-markdown text-[15px] leading-8 text-[var(--ink)]">
            <ReactMarkdown>{documentText}</ReactMarkdown>
          </div>
        ) : (
          <p className="text-sm leading-7 text-[var(--muted)]">
            {isRoleFocus ? "這個角色目前還沒有輸出。" : "目前還沒有最終文件。"}
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      {isRoleFocus ? (
        <div className="mt-6 space-y-4">
          <DisclosurePanel
            title="工具呼叫"
            badge={state.toolCalls?.length ? `${state.toolCalls.length} 筆` : "0 筆"}
          >
            {state.toolCalls?.length ? (
              <div className="space-y-3">
                {state.toolCalls.map((call, index) => (
                  <div
                    key={`${call.tool_name}-${index}`}
                    className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3"
                  >
                    <div className="font-mono text-xs text-[var(--ink)]">{call.tool_name}</div>
                    <pre className="stream-text mt-3 whitespace-pre-wrap text-xs leading-7 text-[var(--muted)]">
                      {JSON.stringify(call.args || {}, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm leading-7 text-[var(--muted)]">這個角色目前沒有工具呼叫紀錄。</p>
            )}
          </DisclosurePanel>

          {plan ? (
            <DisclosurePanel title="協調計畫" badge="orchestrator">
              <pre className="stream-text whitespace-pre-wrap text-xs leading-7 text-[var(--ink)]">
                {JSON.stringify(plan, null, 2)}
              </pre>
            </DisclosurePanel>
          ) : null}

          <VerificationSnapshot
            verdict={verdict}
            verificationSummary={verificationSummary}
            consensusFindings={consensusFindings}
            disputedFindings={disputedFindings}
            convergenceMetrics={convergenceMetrics}
            driftSummary={driftSummary}
            defaultOpen={false}
            title="整體驗收與收斂"
          />
        </div>
      ) : (
        <div className="mt-6">
          <VerificationSnapshot
            verdict={verdict}
            verificationSummary={verificationSummary}
            consensusFindings={consensusFindings}
            disputedFindings={disputedFindings}
            convergenceMetrics={convergenceMetrics}
            driftSummary={driftSummary}
            defaultOpen={true}
            title="驗收結果與收斂"
          />
        </div>
      )}
    </article>
  );
}
