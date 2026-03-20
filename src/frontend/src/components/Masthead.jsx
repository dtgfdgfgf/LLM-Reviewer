export function Masthead({
  theme,
  onToggleTheme,
  infoOpen,
  onToggleInfo,
  appInfo,
  shutdownStatus,
  onShutdown,
}) {
  return (
    <header className="relative px-4 pt-4 sm:px-6">
      <div className="paper-panel-strong overflow-visible px-5 py-4 sm:px-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-2">
              <p className="paper-kicker">本機 review 工作台</p>
              <span className="meta-pill">GitHub Copilot SDK</span>
            </div>
            <h1 className="mt-2 font-display text-3xl leading-none text-[var(--ink)] sm:text-4xl">
              Reviewer
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-[var(--muted)]">
              先選目標、再決定模式與模型設定，最後直接產生一份可閱讀的工程 review 報告。
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 lg:justify-end">
            <button type="button" className="ghost-button" onClick={onToggleInfo}>
              {infoOpen ? "收合說明" : "使用說明"}
            </button>
            <button type="button" className="ghost-button" onClick={onToggleTheme}>
              {theme === "dark" ? "切換淺色" : "切換深色"}
            </button>
            {appInfo.packaged && appInfo.shutdown_supported ? (
              <button
                type="button"
                className="ghost-button"
                disabled={shutdownStatus === "pending"}
                onClick={onShutdown}
              >
                {shutdownStatus === "pending" ? "關閉中..." : "關閉應用"}
              </button>
            ) : null}
          </div>
        </div>

        {infoOpen ? (
          <div className="editor-divider mt-4 pt-4">
            <div className="grid gap-4 lg:grid-cols-2">
              <section className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-4">
                <p className="paper-kicker">運作方式</p>
                <p className="mt-3 text-sm leading-7 text-[var(--muted)]">
                  前端會把本機路徑或 uploaded_files 送到 FastAPI，然後用 SSE 接收每個角色的即時輸出，
                  你可以一邊看報告草稿，一邊查看各角色的執行紀錄。
                </p>
              </section>
              <section className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-4">
                <p className="paper-kicker">保留的功能</p>
                <ul className="mt-3 space-y-2 text-sm leading-7 text-[var(--muted)]">
                  <li>支援資料夾、檔案清單、uploaded_files 三種來源。</li>
                  <li>支援一般模式與 LLM Repo 模式，並保留即時 SSE 更新。</li>
                  <li>支援模型覆寫、metrics、Markdown 下載與 packaged shutdown。</li>
                </ul>
              </section>
            </div>
          </div>
        ) : null}
      </div>
    </header>
  );
}
