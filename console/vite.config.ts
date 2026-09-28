import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev: the browser only ever talks to Vite's origin; /api is proxied to Caddy, so the
// console and every API still share one origin (D16) and no CORS is involved.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/api": process.env.VEYRA_API_ORIGIN ?? "http://localhost:8080" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    // Node 25 ships its own localStorage, which warns on every access without
    // --localstorage-file; the tests use jsdom's.
    execArgv: ["--no-experimental-webstorage"],
  },
});
