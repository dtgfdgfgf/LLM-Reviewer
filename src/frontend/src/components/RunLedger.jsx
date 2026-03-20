import { LensPanel } from "./LensPanel.jsx";

export function RunLedger({ sections, expandedKey, onOpen }) {
  return (
    <aside className="min-h-0 overflow-y-auto pb-6">
      <div className="paper-panel-strong h-full">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="paper-kicker">執行紀錄</p>
            <h2 className="mt-2 font-display text-3xl text-[var(--ink)]">即時角色</h2>
          </div>
          <span className="meta-pill">點擊切換中央內容</span>
        </div>

        <div className="mt-6 space-y-6">
          {sections.map((section) => (
            <section key={section.title}>
              <div className="mb-3 flex items-center justify-between gap-3">
                <h3 className="text-xs font-semibold uppercase tracking-[0.22em] text-[var(--muted)]">
                  {section.title}
                </h3>
                {section.note ? <span className="meta-pill">{section.note}</span> : null}
              </div>
              <div className="space-y-3">
                {section.items.map((item) => (
                  <LensPanel
                    key={item.key}
                    lensKey={item.key}
                    title={item.title}
                    subtitle={item.subtitle}
                    state={item.state}
                    metrics={item.metrics}
                    timer={item.timer}
                    selected={expandedKey === item.key}
                    onOpen={() => onOpen(item.key)}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </aside>
  );
}
