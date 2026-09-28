import { defineConfig, devices } from "@playwright/test";

// The smoke runs against mock mode, so it needs no stack: `make console-e2e`.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: { baseURL: "http://localhost:5173", trace: "retain-on-failure" },
  webServer: {
    command: "npm run dev:mock -- --port 5173 --strictPort",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
