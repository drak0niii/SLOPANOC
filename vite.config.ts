import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  appType: "spa", // Direct console links and browser reload load the Resources console entry.
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // Receipt writes remain on ANOC; every other observability request goes
      // to the separate reader. These entries must precede the /api fallback.
      "^/api/observability/sse-receipts(?:[/?]|$)": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "^/api/observability(?:[/?]|$)": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
      // Phase 4F: forwards the frontend's same-origin "/api/..." calls to
      // the local FastAPI backend in development. No CORS middleware
      // exists on the backend (deliberately — see backend/api/app.py), so
      // this proxy is how the browser reaches it, not cross-origin CORS.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
