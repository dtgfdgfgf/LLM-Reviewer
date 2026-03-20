import { useState } from "react";

const ROLE_METADATA = {
  orchestrator: {
    label: "Orchestrator",
    tooltip: "負責規劃 review 範圍，Auto 模式下也會動態挑模型。",
  },
  reviewer_1: {
    label: "Architecture",
    tooltip: "一般模式的架構視角。",
  },
  reviewer_2: {
    label: "Backend",
    tooltip: "一般模式的後端與服務視角。",
  },
  reviewer_3: {
    label: "Frontend",
    tooltip: "一般模式的前端與可用性視角。",
  },
  synthesizer: {
    label: "Synthesizer",
    tooltip: "整合 reviewer 輸出，產出最終報告。",
  },
  spec_drift: {
    label: "Spec Drift",
    tooltip: "檢查文件、測試與實作之間的規格漂移。",
  },
  architecture_integrity: {
    label: "Architecture",
    tooltip: "檢查模組邊界、分層與耦合。",
  },
  security_boundary: {
    label: "Security",
    tooltip: "檢查 auth、信任邊界與 secret handling。",
  },
  runtime_operational: {
    label: "Runtime",
    tooltip: "檢查 CI、執行期假設與失敗模式。",
  },
  test_integrity: {
    label: "Test Integrity",
    tooltip: "檢查測試是否真的驗證到應驗證的事情。",
  },
  llm_artifact_simplification: {
    label: "LLM Artifact",
    tooltip: "找出多餘抽象與 generated residue。",
  },
  challenger: {
    label: "Challenger",
    tooltip: "質疑證據不足或下結論過快的 findings。",
  },
  judge: {
    label: "Judge",
    tooltip: "產出嚴格模式 verdict 與 convergence summary。",
  },
};

const PROFILE_ROLE_GROUPS = {
  general: ["orchestrator", "reviewer_1", "reviewer_2", "reviewer_3", "synthesizer"],
  llm_repo: [
    "spec_drift",
    "architecture_integrity",
    "security_boundary",
    "runtime_operational",
    "test_integrity",
    "llm_artifact_simplification",
    "challenger",
    "judge",
  ],
};

function EstimateMetric({ label, value }) {
  return (
    <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[var(--muted)]">
        {label}
      </div>
      <div className="mt-2 text-xl font-semibold text-[var(--ink)]">{value}</div>
    </div>
  );
}

function fmtRange(min, max) {
  return min === max ? `${min}` : `${min}-${max}`;
}

function fmtDecimalRange(min, max) {
  const a = Number(min || 0).toFixed(1);
  const b = Number(max || 0).toFixed(1);
  return a === b ? a : `${a}-${b}`;
}

export function ModelRouterPanel({
  config,
  onChange,
  models,
  disabled,
  reviewerNames = [],
  reviewProfile = "llm_repo",
  estimate = null,
  estimateStatus = "idle",
  estimateError = null,
  byokActive = false,
}) {
  const [panelOpen, setPanelOpen] = useState(false);

  const roleLabels = {
    orchestrator: ROLE_METADATA.orchestrator.label,
    reviewer_1: reviewerNames[0] ?? ROLE_METADATA.reviewer_1.label,
    reviewer_2: reviewerNames[1] ?? ROLE_METADATA.reviewer_2.label,
    reviewer_3: reviewerNames[2] ?? ROLE_METADATA.reviewer_3.label,
    synthesizer: ROLE_METADATA.synthesizer.label,
    spec_drift: ROLE_METADATA.spec_drift.label,
    architecture_integrity: ROLE_METADATA.architecture_integrity.label,
    security_boundary: ROLE_METADATA.security_boundary.label,
    runtime_operational: ROLE_METADATA.runtime_operational.label,
    test_integrity: ROLE_METADATA.test_integrity.label,
    llm_artifact_simplification: ROLE_METADATA.llm_artifact_simplification.label,
    challenger: ROLE_METADATA.challenger.label,
    judge: ROLE_METADATA.judge.label,
  };

  const presets = [
    {
      value: "balanced",
      label: "平衡",
      detail: "品質、速度與成本較均衡，適合大多數情境。",
    },
    {
      value: "economy",
      label: "節省",
      detail: "偏向較低成本，適合快速掃描或大 repo。",
    },
    {
      value: "performance",
      label: "效能",
      detail: "偏向較高能力模型，適合深度 review。",
    },
    {
      value: "free",
      label: "Free",
      detail: "優先使用可用的零成本模型。",
    },
    {
      value: "auto",
      label: "Auto",
      detail: "讓 Orchestrator 在執行時動態選擇模型。",
    },
  ];

  const roles = PROFILE_ROLE_GROUPS[reviewProfile] || PROFILE_ROLE_GROUPS.llm_repo;
  const allSameOverride = (() => {
    const values = roles.map((role) => config.overrides?.[role] || "");
    const uniqueValues = [...new Set(values.filter(Boolean))];
    return uniqueValues.length === 1 ? uniqueValues[0] : "";
  })();

  function handlePreset(preset) {
    onChange({
      ...config,
      preset,
      overrides: {},
      globalModel: "",
      advancedOpen: config.advancedOpen ?? false,
    });
  }

  function handleGlobalModel(model) {
    const overrides = model
      ? Object.fromEntries(roles.map((role) => [role, model]))
      : {};
    onChange({
      ...config,
      overrides,
      globalModel: model || "",
    });
  }

  function handleOverride(role, model) {
    const nextOverrides = { ...config.overrides, [role]: model || undefined };
    const values = roles.map((item) => nextOverrides[item]).filter(Boolean);
    const globalModel = values.length === roles.length && new Set(values).size === 1 ? values[0] : "";
    onChange({
      ...config,
      overrides: nextOverrides,
      globalModel,
    });
  }

  const activePreset = presets.find((preset) => preset.value === config.preset);

  return (
    <section className="paper-panel space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="paper-kicker">模型設定</p>
          <h3 className="mt-2 font-display text-2xl text-[var(--ink)]">模型進階設定</h3>
        </div>
        <button
          type="button"
          className="subtle-button"
          onClick={() => setPanelOpen((open) => !open)}
        >
          {panelOpen ? "收合進階" : "更多設定"}
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {presets.map((preset) => (
          <button
            key={preset.value}
            type="button"
            onClick={() => handlePreset(preset.value)}
            disabled={disabled}
            className={`rounded-2xl border px-4 py-3 text-left transition-colors ${
              config.preset === preset.value
                ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--ink)]"
                : "border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:border-[var(--accent)]/60 hover:text-[var(--ink)]"
            } disabled:cursor-not-allowed disabled:opacity-50`}
          >
            <div className="text-xs font-semibold uppercase tracking-[0.22em]">{preset.label}</div>
            <div className="mt-2 text-sm leading-7">{preset.detail}</div>
          </button>
        ))}
      </div>

      <div className="rounded-[24px] border border-[var(--line)] bg-[var(--surface)] px-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="paper-kicker">送出前預估</div>
            <p className="mt-2 text-sm leading-7 text-[var(--muted)]">
              {estimateStatus === "loading"
                ? "正在更新目前設定的預估成本。"
                : estimateStatus === "error"
                  ? "目前無法取得預估。"
                  : estimate
                    ? "已依目前來源與模型設定更新。"
                    : "等來源準備完成後，這裡會出現預估資訊。"}
            </p>
          </div>
          <span className="meta-pill">{activePreset?.label || config.preset}</span>
        </div>

        {estimate ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <EstimateMetric
              label="Sessions"
              value={fmtRange(estimate.estimated_sessions_min, estimate.estimated_sessions_max)}
            />
            <EstimateMetric
              label={byokActive ? "Turns" : "Premium reqs"}
              value={
                byokActive
                  ? fmtRange(estimate.estimated_turns_min, estimate.estimated_turns_max)
                  : fmtDecimalRange(estimate.estimated_pru_min, estimate.estimated_pru_max)
              }
            />
          </div>
        ) : null}

        {estimateStatus === "error" ? (
          <p className="mt-3 text-sm leading-7 text-[var(--danger)]">{estimateError}</p>
        ) : null}
      </div>

      {panelOpen ? (
        <div className="space-y-5">
          <div className="editor-divider pt-5">
            <p className="paper-kicker">統一模型</p>
            <select
              value={config.globalModel || allSameOverride || ""}
              onChange={(event) => handleGlobalModel(event.target.value)}
              disabled={disabled || models.length === 0}
              className="paper-select mt-3"
            >
              <option value="">
                {models.length === 0 ? "後端目前離線" : "沿用 preset 預設"}
              </option>
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name || model.id}
                </option>
              ))}
            </select>
            <p className="mt-3 text-sm leading-7 text-[var(--muted)]">
              選定後，會套用到目前模式會啟用的所有角色。
            </p>
          </div>

          {estimate?.role_estimates?.length > 0 ? (
            <div className="editor-divider pt-5">
              <p className="paper-kicker">角色別預估</p>
              <div className="mt-3 space-y-2">
                {estimate.role_estimates.map((item) => (
                  <div
                    key={item.role}
                    title={(item.notes || []).join(" ") || `${item.display_name} estimate`}
                    className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="font-semibold text-[var(--ink)]">{item.display_name}</div>
                        <div className="mt-1 text-xs text-[var(--muted)]">
                          {item.optional ? "選配角色" : "固定角色"}
                        </div>
                      </div>
                      <div className="text-right font-mono text-xs text-[var(--muted)]">
                        <div>{fmtRange(item.estimated_sessions_min, item.estimated_sessions_max)} sess</div>
                        <div>
                          {byokActive
                            ? `${fmtRange(item.estimated_turns_min, item.estimated_turns_max)} turns`
                            : `${fmtDecimalRange(item.estimated_pru_min, item.estimated_pru_max)} PR`}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              {estimate.notes?.length > 0 ? (
                <p className="mt-3 text-sm leading-7 text-[var(--muted)]">{estimate.notes.join(" ")}</p>
              ) : null}
            </div>
          ) : null}

          <div className="editor-divider pt-5">
            <button
              type="button"
              className="subtle-button w-full justify-between"
              onClick={() => onChange({ ...config, advancedOpen: !config.advancedOpen })}
            >
              <span>{config.advancedOpen ? "收合角色別覆寫" : "角色別模型覆寫"}</span>
              <span>{config.advancedOpen ? "−" : "+"}</span>
            </button>

            {config.advancedOpen ? (
              <div className="mt-4 space-y-3">
                {roles.map((role) => (
                  <div
                    key={role}
                    className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3"
                  >
                    <div className="flex flex-col gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-[var(--ink)]">{roleLabels[role] ?? role}</span>
                          <span className="meta-pill">{role}</span>
                        </div>
                        <p className="mt-2 text-sm leading-7 text-[var(--muted)]">
                          {ROLE_METADATA[role]?.tooltip || role}
                        </p>
                      </div>
                      <select
                        value={config.overrides?.[role] || ""}
                        onChange={(event) => handleOverride(role, event.target.value)}
                        disabled={disabled || models.length === 0}
                        className="paper-select"
                      >
                        <option value="">
                          {models.length === 0 ? "後端目前離線" : "沿用 preset 預設"}
                        </option>
                        {models.map((model) => (
                          <option key={model.id} value={model.id}>
                            {model.name || model.id}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>

          {config.preset === "auto" ? (
            <p className="text-sm leading-7 text-[var(--muted)]">
              Auto 仍會讓 Orchestrator 在執行時挑選模型；如果你有手動覆寫，手動設定優先。
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
