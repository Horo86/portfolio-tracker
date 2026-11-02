import { defineConfig, devices } from "@playwright/test";

// The automated walkthrough of the M5 acceptance path (D-159): one scenario on a server of its
// own, which the spec starts, stops and restarts itself (e2e/walkthrough.spec.ts), on a fresh
// development data directory, market data off. Run with `npm run e2e:walkthrough` (builds first).
export const WALKTHROUGH = "http://127.0.0.1:8713";

export default defineConfig({
  testDir: "e2e",
  testMatch: "walkthrough.spec.ts",
  workers: 1,
  forbidOnly: true,
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: WALKTHROUGH,
    locale: "it-IT",
    trace: "retain-on-failure",
    // One long scenario: a step that cannot act fails in seconds, not at the test's timeout.
    actionTimeout: 15_000,
  },
});
