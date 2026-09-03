import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
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
