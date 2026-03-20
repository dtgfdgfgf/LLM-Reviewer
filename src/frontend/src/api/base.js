export function getApiBase() {
  const override = import.meta.env.VITE_API_BASE;
  if (override) return override.replace(/\/$/, "");
  return "/api";
}

export function toApiUrl(path) {
  if (!path) return path;
  if (/^https?:\/\//.test(path)) return path;
  if (path.startsWith("/")) return path;
  return `${getApiBase()}/${path.replace(/^\//, "")}`;
}
