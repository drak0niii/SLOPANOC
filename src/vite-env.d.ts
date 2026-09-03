/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional backend API base URL override — see src/api/config.ts.
   * Left unset in normal local development (same-origin relative
   * "/api/..." calls, proxied by Vite — see vite.config.ts). */
  readonly VITE_SLOPANOC_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
