import { useState } from "react";
import { CopyButton } from "./AgentPanel.jsx";

function modeLabel(mode) {
  return mode === "byok" ? "BYOK" : "Copilot CLI";
}

export function AuthStatusCard({ status, validating, disabled, onValidate }) {
  const [detailsOpen, setDetailsOpen] = useState(false);

  if (!status) return null;

  return (
    <section className="paper-panel space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="paper-kicker">執行環境</p>
          <h3 className="mt-2 font-display text-2xl text-[var(--ink)]">登入與模型狀態</h3>
        </div>

        <button
          type="button"
          className="subtle-button"
          onClick={onValidate}
          disabled={disabled || validating}
        >
          {validating ? "檢查中..." : "重新檢查"}
        </button>
      </div>

      <div className="rounded-[24px] border border-[var(--line)] bg-[var(--surface)] px-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="meta-pill">
              <span className={`status-dot ${status.ready ? "complete" : "error"}`} />
              {status.ready ? "可使用" : "需要處理"}
            </span>
            <span className="meta-pill">{modeLabel(status.mode)}</span>
          </div>
          <span className="font-mono text-xs text-[var(--muted)]">
            {status.models_count} 個模型
          </span>
        </div>
        <p className="mt-3 text-sm leading-7 text-[var(--muted)]">{status.message}</p>
      </div>

      <button
        type="button"
        className="subtle-button w-full justify-between"
        onClick={() => setDetailsOpen((open) => !open)}
      >
        <span>{detailsOpen ? "收合詳細狀態" : "查看詳細狀態"}</span>
        <span>{detailsOpen ? "−" : "+"}</span>
      </button>

      {detailsOpen ? (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              ["已連線", String(status.copilot_connected)],
              ["CLI 可用", String(status.copilot_cli_detected)],
              ["BYOK 啟用", String(status.byok_active)],
              ["模式", modeLabel(status.mode)],
            ].map(([label, value]) => (
              <div
                key={label}
                className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3"
              >
                <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[var(--muted)]">
                  {label}
                </div>
                <div className="mt-2 font-mono text-sm text-[var(--ink)]">{value}</div>
              </div>
            ))}
          </div>

          {status.suggested_actions?.length > 0 ? (
            <div className="space-y-3">
              <p className="paper-kicker">建議操作</p>
              {status.suggested_actions.map((action) => (
                <div
                  key={action}
                  className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm leading-7 text-[var(--muted)]">{action}</p>
                    <CopyButton text={action} />
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
