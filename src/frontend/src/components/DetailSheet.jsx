import { useEffect } from "react";

export function DetailSheet({ open, onClose, title, subtitle, meta = [], children }) {
  useEffect(() => {
    if (!open) return undefined;

    function onKeyDown(event) {
      if (event.key === "Escape") onClose?.();
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-[color:rgba(24,20,17,0.35)] backdrop-blur-sm">
      <button
        type="button"
        aria-label="關閉詳細面板"
        className="absolute inset-0"
        onClick={onClose}
      />
      <section className="relative z-10 flex h-full w-full max-w-[560px] flex-col border-l border-[var(--line)] bg-[var(--surface)] shadow-[0_28px_80px_rgba(39,30,21,0.18)]">
        <div className="border-b border-[var(--line)] px-5 py-4 sm:px-6">
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-2">
              <p className="paper-kicker">角色詳情</p>
              <div>
                <h2 className="font-display text-2xl text-[var(--ink)]">{title}</h2>
                {subtitle ? (
                  <p className="mt-1 text-sm text-[var(--muted)]">{subtitle}</p>
                ) : null}
              </div>
              {meta.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {meta.map((item) => (
                    <span key={item} className="meta-pill">
                      {item}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
            <button type="button" className="ghost-button" onClick={onClose}>
              關閉
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">{children}</div>
      </section>
    </div>
  );
}
