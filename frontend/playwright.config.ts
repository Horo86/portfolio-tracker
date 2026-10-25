import { defineConfig, devices } from "@playwright/test";

// End-to-end tests in Chromium against the built app served by FastAPI (M5 planning, Q-8).
// Three servers on fresh development data directories: an empty one for the first run, one
// with the invented development data, and an empty one with market data answered by invented
// fakes (backend/tests/e2e_market_server.py). Run with `npm run e2e` (builds first).
export const EMPTY = "http://127.0.0.1:8710";
export const SEEDED = "http://127.0.0.1:8711";
export const MARKET = "http://127.0.0.1:8712";

export default defineConfig({
  testDir: "e2e",
  // One at a time: the specs share the seeded server, its users and its login throttle.
  workers: 1,
  fullyParallel: false,
  forbidOnly: true,
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: SEEDED,
    locale: "en-US",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "bash e2e/serve.sh empty 8710",
      url: `${EMPTY}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "bash e2e/serve.sh seeded 8711",
      url: `${SEEDED}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "bash e2e/serve.sh market 8712",
      url: `${MARKET}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
