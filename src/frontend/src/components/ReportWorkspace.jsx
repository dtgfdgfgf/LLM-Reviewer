export function ReportWorkspace({
  reviewStatus,
  reviewProfile,
  roleLabel,
  focusMode = "final",
  focusSubtitle = "",
  draftReviewProfile,
  reportContent,
  children,
}) {
  if (reviewStatus === "idle") {
    return (
      <section className="min-h-0">
        <div className="paper-panel-strong h-full space-y-5">
          <div>
            <p className="paper-kicker">主要流程</p>
            <h2 className="mt-2 font-display text-3xl leading-tight text-[var(--ink)] sm:text-4xl">
              先選目標，再開始 review。
            </h2>
            <p className="mt-4 max-w-2xl text-sm leading-7 text-[var(--muted)]">
              左側先完成 review 目標、模式與必要設定；送出後，中間會顯示報告草稿，右側則保留每個角色的執行紀錄。
            </p>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
            <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-4">
              <div className="flex flex-wrap gap-2">
                <span className="meta-pill">1. 選目標</span>
                <span className="meta-pill">2. 選模式</span>
                <span className="meta-pill">3. 開始 review</span>
              </div>
              <p className="mt-4 text-sm leading-7 text-[var(--muted)]">
                目前左側欄位預設為{" "}
                <span className="font-semibold text-[var(--ink)]">
                  {draftReviewProfile === "llm_repo" ? "LLM Repo" : "一般模式"}
                </span>
                ，必要操作都保持在初始畫面可見；較少用的模型細節會收在可展開區塊裡。
              </p>
            </div>

            <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-4">
              <p className="paper-kicker">支援能力</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <span className="meta-pill">資料夾 / 檔案</span>
                <span className="meta-pill">uploaded_files</span>
                <span className="meta-pill">即時 SSE</span>
                <span className="meta-pill">Markdown 匯出</span>
              </div>
            </div>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="min-h-0 overflow-y-auto pb-6">
      <div className="space-y-4">
        <div className="paper-panel-strong">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="paper-kicker">
                {focusMode === "role"
                  ? reviewStatus === "running"
                    ? "角色 Session 進行中"
                    : "角色 Session 檢視"
                  : reviewStatus === "running"
                    ? "報告產生中"
                    : "報告已完成"}
              </p>
              <h2 className="mt-2 font-display text-4xl text-[var(--ink)]">
                {roleLabel}
              </h2>
              <p className="mt-3 text-sm leading-7 text-[var(--muted)]">
                {focusMode === "role"
                  ? focusSubtitle ||
                    "中央主區目前固定顯示這個角色的 Session 報告與即時輸出。"
                  : reviewStatus === "running"
                    ? "中間主區專注顯示報告草稿，右側則同步更新各角色的執行紀錄。"
                    : "review 已完成，你可以複製、下載，或回頭查看每個角色的執行細節。"}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className="meta-pill">{reviewProfile === "llm_repo" ? "LLM Repo" : "一般模式"}</span>
              <span className="meta-pill">
                {reportContent ? `${reportContent.length.toLocaleString()} 字元` : "等待報告"}
              </span>
            </div>
          </div>
        </div>

        {children}
      </div>
    </section>
  );
}
