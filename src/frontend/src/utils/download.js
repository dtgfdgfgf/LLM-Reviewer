export function downloadFile(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadMarkdown(filename, content) {
  downloadFile(filename, content, "text/markdown;charset=utf-8");
}

export function downloadJson(filename, payload) {
  downloadFile(filename, JSON.stringify(payload, null, 2), "application/json;charset=utf-8");
}
