import { defineConfig, devices } from "@playwright/test";

// The smoke and the Beat 2/4 flows run against mock mode, so they need no stack: `make console-e2e`.
// One worker: the dev server compiles the app on first load, and parallel cold loads blow the
// timeout on the demo laptop. The flows assert their own on-stage timings (C6 AC1/AC2).
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60_000,
  use: { baseURL: "http://localhost:5173", trace: "retain-on-failure" },
  webServer: {
    command: "npm run dev:mock -- --port 5173 --strictPort",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
