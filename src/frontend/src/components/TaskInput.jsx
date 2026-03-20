import { useEffect, useMemo, useState } from "react";
import { pickFiles, pickFolder } from "../api/client.js";

const MODEL_ROLE_GROUPS = {
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

const MAX_CLIENT_FILE_BYTES = 1024 * 1024;
const UNSUPPORTED_EXTENSIONS = new Set([
  ".7z",
  ".avi",
  ".bmp",
  ".doc",
  ".docx",
  ".exe",
  ".gif",
  ".gz",
  ".jpeg",
  ".jpg",
  ".mov",
  ".mp3",
  ".mp4",
  ".otf",
  ".pdf",
  ".png",
  ".sqlite",
  ".tar",
  ".ttf",
  ".webm",
  ".webp",
  ".woff",
  ".woff2",
  ".xls",
  ".xlsx",
  ".zip",
]);

function basename(path) {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function isLikelyUnsupported(path) {
  const normalized = path.toLowerCase();
  for (const extension of UNSUPPORTED_EXTENSIONS) {
    if (normalized.endsWith(extension)) return true;
  }
  return false;
}

function parseManualFilePaths(value) {
  return value
    .split("\n")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

async function readDroppedFiles(fileList) {
  const files = Array.from(fileList ?? []);
  const uploadedFiles = [];

  for (const file of files) {
    if (isLikelyUnsupported(file.name)) {
      throw new Error(`不支援的檔案類型：${file.name}`);
    }
    if (file.size > MAX_CLIENT_FILE_BYTES) {
      throw new Error(`檔案過大：${file.name} 超過 1 MB`);
    }

    const content = await file.text();
    if (content.includes("\u0000")) {
      throw new Error(`不支援二進位檔案：${file.name}`);
    }

    uploadedFiles.push({
      name: file.name,
      content,
    });
  }

  return uploadedFiles;
}

function SegmentButton({ active, disabled, onClick, label, hint }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-2xl border px-3 py-3 text-left transition-all ${
        active
          ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--ink)]"
          : "border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:border-[var(--accent)]/60 hover:text-[var(--ink)]"
      } disabled:cursor-not-allowed disabled:opacity-50`}
    >
      <div className="text-xs font-semibold uppercase tracking-[0.22em]">{label}</div>
      <div className="mt-1 text-xs leading-6">{hint}</div>
    </button>
  );
}

export function TaskInput({
  onSubmit,
  disabled,
  packaged,
  reviewProfile = "llm_repo",
  onReviewProfileChange,
  onDraftChange,
  modelConfig,
  onModelConfigChange,
  models = [],
}) {
  const [sourceMode, setSourceMode] = useState("folder");
  const [folderPath, setFolderPath] = useState("");
  const [filePaths, setFilePaths] = useState([]);
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [focusPrompt, setFocusPrompt] = useState("");
  const [focusPromptOpen, setFocusPromptOpen] = useState(false);
  const [error, setError] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [readingDrop, setReadingDrop] = useState(false);
  const [pickerBusy, setPickerBusy] = useState(false);
  const manualFilesValue = useMemo(() => filePaths.join("\n"), [filePaths]);
  const focusPromptSummary = focusPrompt.trim()
    ? `已設定重點：${focusPrompt.trim().slice(0, 24)}${focusPrompt.trim().length > 24 ? "..." : ""}`
    : "選填：加入 review 重點 Focus Prompt";

  const sourceHint = packaged
    ? "可直接貼上本機絕對路徑，或用 Windows 選擇器挑選。"
    : "開發模式下請直接輸入本機絕對路徑。";

  useEffect(() => {
    const trimmedFolder = folderPath.trim();
    const trimmedPrompt = focusPrompt.trim();
    const effectiveSourceMode =
      sourceMode === "files" && uploadedFiles.length > 0 ? "uploaded_files" : sourceMode;
    const draft = {
      review_profile: reviewProfile,
      source_mode: effectiveSourceMode,
      folder_path: effectiveSourceMode === "folder" ? trimmedFolder || undefined : undefined,
      file_paths: effectiveSourceMode === "files" ? filePaths : undefined,
      uploaded_files: effectiveSourceMode === "uploaded_files" ? uploadedFiles : undefined,
      focus_prompt: trimmedPrompt || undefined,
      ready:
        (effectiveSourceMode === "folder" && Boolean(trimmedFolder)) ||
        (effectiveSourceMode === "files" && filePaths.length > 0) ||
        (effectiveSourceMode === "uploaded_files" && uploadedFiles.length > 0),
    };
    onDraftChange?.(draft);
  }, [
    filePaths,
    folderPath,
    focusPrompt,
    onDraftChange,
    reviewProfile,
    sourceMode,
    uploadedFiles,
  ]);

  function setMode(nextMode) {
    setSourceMode(nextMode);
    setError("");
    if (nextMode === "folder") setUploadedFiles([]);
  }

  function handleQuickModelChange(model) {
    if (!onModelConfigChange || !modelConfig) return;
    const roles = MODEL_ROLE_GROUPS[reviewProfile] || MODEL_ROLE_GROUPS.llm_repo;
    const overrides = model
      ? Object.fromEntries(roles.map((role) => [role, model]))
      : {};
    onModelConfigChange({
      ...modelConfig,
      overrides,
      globalModel: model || "",
    });
  }

  async function handlePickFolder() {
    setPickerBusy(true);
    setError("");
    try {
      const result = await pickFolder();
      if (result.selected && result.folder_path) {
        setFolderPath(result.folder_path);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setPickerBusy(false);
    }
  }

  async function handlePickFiles() {
    setPickerBusy(true);
    setError("");
    try {
      const result = await pickFiles();
      if (result.selected && Array.isArray(result.file_paths)) {
        setFilePaths(result.file_paths);
        setUploadedFiles([]);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setPickerBusy(false);
    }
  }

  async function handleDrop(event) {
    event.preventDefault();
    setDragActive(false);
    setError("");
    setReadingDrop(true);

    try {
      const nextUploads = await readDroppedFiles(event.dataTransfer.files);
      if (nextUploads.length === 0) {
        throw new Error("沒有收到可用的檔案。");
      }
      setSourceMode("files");
      setUploadedFiles(nextUploads);
      setFilePaths([]);
    } catch (err) {
      setError(err.message);
    } finally {
      setReadingDrop(false);
    }
  }

  function handleSubmit(event) {
    event.preventDefault();
    setError("");

    const strictReviewConfig =
      reviewProfile === "llm_repo"
        ? {
            review_profile: "llm_repo",
            evidence_mode: "static_runtime",
            output_mode: "structured_report",
            gate_mode: "blocking",
            convergence_mode: "adaptive_rerun",
          }
        : {
            review_profile: "general",
            evidence_mode: "static_first",
            output_mode: "report",
            gate_mode: "advisory",
            convergence_mode: "single_pass",
          };

    if (sourceMode === "folder") {
      if (!folderPath.trim()) {
        setError("請先選擇要 review 的資料夾。");
        return;
      }

      onSubmit({
        ...strictReviewConfig,
        source_mode: "folder",
        folder_path: folderPath.trim(),
        focus_prompt: focusPrompt.trim() || undefined,
      });
      return;
    }

    if (uploadedFiles.length === 0 && filePaths.length === 0) {
      setError("請先選擇至少一個檔案。");
      return;
    }

    const unsupportedFile = filePaths.find(isLikelyUnsupported);
    if (unsupportedFile) {
      setError(`不支援的檔案類型：${basename(unsupportedFile)}`);
      return;
    }

    if (uploadedFiles.length > 0) {
      onSubmit({
        ...strictReviewConfig,
        source_mode: "uploaded_files",
        uploaded_files: uploadedFiles,
        focus_prompt: focusPrompt.trim() || undefined,
      });
      return;
    }

    onSubmit({
      ...strictReviewConfig,
      source_mode: "files",
      file_paths: filePaths,
      focus_prompt: focusPrompt.trim() || undefined,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="paper-panel-strong space-y-5">
      <div>
        <p className="paper-kicker">主要流程</p>
        <h3 className="mt-2 font-display text-2xl text-[var(--ink)]">設定 review 內容</h3>
      </div>

      <div className="grid gap-4">
        <div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="paper-kicker">review 模式</p>
            <span className="text-xs text-[var(--muted)]">模式與模型可一起決定</span>
          </div>
          <div className="mt-3 space-y-3">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 2xl:grid-cols-2">
              <SegmentButton
                active={reviewProfile === "llm_repo"}
                disabled={disabled || pickerBusy || readingDrop}
                onClick={() => onReviewProfileChange?.("llm_repo")}
                label="LLM Repo"
                hint="較嚴格，含 blocking verdict 與結構化 findings。"
              />
              <SegmentButton
                active={reviewProfile === "general"}
                disabled={disabled || pickerBusy || readingDrop}
                onClick={() => onReviewProfileChange?.("general")}
                label="一般模式"
                hint="適合較廣泛的工程 review，流程較精簡。"
              />
            </div>

            <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="paper-kicker">模型</p>
                  <p className="mt-2 text-sm leading-7 text-[var(--muted)]">
                    先選一個統一模型，細部覆寫可在下方調整。
                  </p>
                </div>
                <span className="meta-pill">{modelConfig?.preset || "balanced"}</span>
              </div>
              <select
                value={modelConfig?.globalModel || ""}
                onChange={(event) => handleQuickModelChange(event.target.value)}
                disabled={disabled || models.length === 0}
                className="paper-select mt-3"
              >
                <option value="">
                  {models.length === 0 ? "後端目前離線" : "沿用目前 preset"}
                </option>
                {models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name || model.id}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="editor-divider pt-5">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="paper-kicker">review 目標</p>
            <span className="text-xs leading-5 text-[var(--muted)] sm:max-w-[11rem] sm:text-right">
              {sourceHint}
            </span>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-1 2xl:grid-cols-2">
            <SegmentButton
              active={sourceMode === "folder"}
              disabled={disabled || pickerBusy || readingDrop}
              onClick={() => setMode("folder")}
              label="資料夾"
              hint="遞迴 review 整個專案資料夾。"
            />
            <SegmentButton
              active={sourceMode === "files"}
              disabled={disabled || pickerBusy || readingDrop}
              onClick={() => setMode("files")}
              label="檔案"
              hint="指定檔案清單，或直接拖放檔案。"
            />
          </div>
        </div>
      </div>

      {sourceMode === "folder" ? (
        <div className="space-y-3">
          <label className="paper-kicker">資料夾路徑</label>
          <div className="flex flex-col gap-3 xl:flex-row">
            <input
              type="text"
              value={folderPath}
              onChange={(event) => setFolderPath(event.target.value)}
              disabled={disabled || pickerBusy}
              placeholder="C:\\repo\\project"
              className="paper-input mono"
            />
            {packaged ? (
              <button
                type="button"
                className="subtle-button shrink-0"
                onClick={handlePickFolder}
                disabled={disabled || pickerBusy}
              >
                {pickerBusy ? "開啟中..." : "瀏覽"}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {sourceMode === "files" ? (
        <div className="space-y-3">
          <label className="paper-kicker">檔案清單</label>
          <div className="flex flex-col gap-3 xl:flex-row">
            <textarea
              value={manualFilesValue}
              onChange={(event) => {
                setFilePaths(parseManualFilePaths(event.target.value));
                setUploadedFiles([]);
              }}
              disabled={disabled || pickerBusy}
              rows={4}
              placeholder={"C:\\repo\\src\\main.py\nC:\\repo\\README.md"}
              className="paper-textarea mono resize-y"
            />
            {packaged ? (
              <button
                type="button"
                className="subtle-button shrink-0"
                onClick={handlePickFiles}
                disabled={disabled || pickerBusy}
              >
                {pickerBusy ? "開啟中..." : "瀏覽"}
              </button>
            ) : null}
          </div>

          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={(event) => {
              event.preventDefault();
              setDragActive(false);
            }}
            onDrop={handleDrop}
            className={`rounded-[24px] border border-dashed px-4 py-4 text-center transition-colors ${
              dragActive
                ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                : "border-[var(--line)] bg-[var(--surface)]"
            }`}
          >
            <div className="text-sm font-semibold text-[var(--ink)]">
              {readingDrop ? "讀取拖放檔案中..." : "拖放文字或程式碼檔案到這裡"}
            </div>
            <div className="mt-1 text-xs leading-6 text-[var(--muted)]">
              會自動改用 uploaded_files 模式；資料夾與二進位檔會被拒絕。
            </div>
          </div>

          {uploadedFiles.length > 0 ? (
            <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3 text-sm text-[var(--muted)]">
              <div className="font-semibold text-[var(--ink)]">目前使用 uploaded_files 內容</div>
              <div className="mt-2 leading-7">
                {uploadedFiles.slice(0, 5).map((file) => file.name).join("、")}
                {uploadedFiles.length > 5 ? `，另有 ${uploadedFiles.length - 5} 個檔案` : ""}
              </div>
              <button
                type="button"
                className="mt-3 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--accent)]"
                onClick={() => setUploadedFiles([])}
              >
                清除 uploaded_files
              </button>
            </div>
          ) : null}

          {filePaths.length > 0 && uploadedFiles.length === 0 ? (
            <p className="font-mono text-xs text-[var(--muted)]">
              已選擇：{filePaths.map(basename).slice(0, 4).join("、")}
              {filePaths.length > 4 ? `，另有 ${filePaths.length - 4} 個檔案` : ""}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="editor-divider pt-5">
        <button
          type="button"
          className="subtle-button w-full justify-between"
          onClick={() => setFocusPromptOpen((open) => !open)}
        >
          <span>{focusPromptSummary}</span>
          <span>{focusPromptOpen ? "−" : "+"}</span>
        </button>

        {focusPromptOpen ? (
          <div className="mt-3">
            <textarea
              value={focusPrompt}
              onChange={(event) => setFocusPrompt(event.target.value)}
              disabled={disabled}
              rows={4}
              placeholder="例如：請特別留意 auth 邊界、非同步流程的可靠性，以及狀態管理的風險。"
              className="paper-textarea resize-y"
            />
            <p className="mt-2 text-sm leading-7 text-[var(--muted)]">
              沒填也可以，系統會做一般工程 review。
            </p>
          </div>
        ) : null}
      </div>

      {error ? (
        <div className="rounded-2xl border border-[var(--danger)]/25 bg-[rgba(181,82,51,0.08)] px-4 py-3 text-sm text-[var(--danger)]">
          {error}
        </div>
      ) : null}

      <div className="editor-divider pt-5">
        <button
          type="submit"
          disabled={disabled || pickerBusy || readingDrop}
          className="cta-button w-full"
        >
          {disabled ? "review 進行中..." : "開始 review"}
        </button>
      </div>
    </form>
  );
}
