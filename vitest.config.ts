import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Kept separate from vite.config.ts (rather than switching that file's
// `defineConfig` import to "vitest/config") so the app's own dev/build
// configuration never depends on a test-only package. The plugin list is
// duplicated here on purpose — a small, accepted tradeoff for that
// decoupling.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
