/**
 * The one seam where the backend's base URL is resolved. Defaults to an
 * empty string (same-origin relative "/api/..." calls, proxied to the
 * backend by Vite's dev server — see vite.config.ts's server.proxy) so
 * local development needs no configuration at all.
 *
 * VITE_SLOPANOC_API_BASE_URL is the only env var this app reads. Every
 * VITE_* value is inlined into the client bundle and is publicly visible —
 * never put secrets (Power Automate URLs, credentials, tokens) here.
 */
export function getApiBaseUrl(): string {
  const raw = import.meta.env.VITE_SLOPANOC_API_BASE_URL;
  if (!raw) return "";
  return raw.trim().replace(/\/+$/, "");
}
