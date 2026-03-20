import { getApiBase } from "./base.js";

/**
 * Start a new code review. Returns { review_id, status, sse_url }.
 */
export async function startReview(payload) {
  const res = await fetch(`${getApiBase()}/reviews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

/**
 * Estimate sessions and premium requests for a review before submission.
 */
export async function fetchReviewEstimate(payload, options = {}) {
  const res = await fetch(`${getApiBase()}/reviews/estimate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: options.signal,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

/**
 * Fetch available Copilot models. Returns { models, byok_active }.
 */
export async function fetchModels() {
  const res = await fetch(`${getApiBase()}/models`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/**
 * Fetch current auth/runtime status.
 */
export async function fetchAuthStatus() {
  const res = await fetch(`${getApiBase()}/auth/status`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/**
 * Perform a real auth/runtime validation pass.
 */
export async function validateAuth() {
  const res = await fetch(`${getApiBase()}/auth/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

/**
 * Health check. Returns { status, copilot_connected }.
 */
export async function healthCheck() {
  const res = await fetch(`${getApiBase()}/health`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/**
 * Fetch runtime info for the local shell.
 */
export async function fetchAppInfo() {
  const res = await fetch(`${getApiBase()}/app/info`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/**
 * Request a graceful shutdown for the packaged app.
 */
export async function shutdownApp() {
  const res = await fetch(`${getApiBase()}/app/shutdown`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

/**
 * Open the packaged app's native folder picker.
 */
export async function pickFolder() {
  const res = await fetch(`${getApiBase()}/app/pick-folder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

/**
 * Open the packaged app's native multi-file picker.
 */
export async function pickFiles() {
  const res = await fetch(`${getApiBase()}/app/pick-files`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}
