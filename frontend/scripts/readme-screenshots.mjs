// The README's screenshots (docs/images/), made again on demand; never in CI. Opt-in and local:
// `npm run screenshots` from the frontend folder (it builds first). It needs the backend's
// environment (`cd backend && uv sync`) and Playwright's Chromium (`npx playwright install
// chromium`, once, as for the end-to-end tests).
//
// It fills a throwaway folder with the invented demo data (backend/scripts/dev_data.py:
// `ZZ` ISINs, made-up names and amounts), starts the app on it with market data off, logs in
// as the demo investor, switches to English and the default light theme, and takes three
// screenshots at 1440x900: the dashboard, the holdings list, and the import preview of the
// public invented Directa sales fixture. Then it stops the server it started and deletes the
// folder. No real data is used anywhere.
import { spawn, spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const FRONTEND = fileURLToPath(new URL("..", import.meta.url));
const BACKEND = `${FRONTEND}/../backend`;
const PYTHON = `${BACKEND}/.venv/bin/python`;
const DATA = `${FRONTEND}/.data-screenshots`; // ignored by git (.data*/)
const IMAGES = `${FRONTEND}/../docs/images`;
const FIXTURE = `${FRONTEND}/../tests/fixtures/directa/directa_synthetic_sales_2025.xlsx`;
const PORT = 8714;
const BASE = `http://127.0.0.1:${PORT}`;
const DEMO = { username: "demo", password: "dev-demo-password" }; // public, in dev_data.py
const CSRF = { "X-Requested-With": "portfolio-tracker" };

/** Fails when something already listens on the port: the shots would be of another server. */
function portFree() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", (error) => reject(new Error(`port ${PORT} is in use (${error.message})`)));
    probe.listen(PORT, "127.0.0.1", () => probe.close(() => resolve()));
  });
}

/** Waits until the server answers, or fails if it exits first. */
async function ready(server) {
  let exited = false;
  server.once("exit", () => (exited = true));
  for (let i = 0; i < 120; i++) {
    if (exited) throw new Error("the server exited before it answered");
    const ok = await fetch(`${BASE}/api/health`).then((r) => r.ok, () => false);
    if (ok) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("the server did not answer within 60 s");
}

async function stop(server) {
  if (server.exitCode !== null) return;
  const gone = new Promise((resolve) => server.once("exit", resolve));
  server.kill("SIGTERM"); // by its own PID, the process this script started
  await gone;
}

async function shoot(page, name) {
  // No animation half way, no caret, the fonts loaded.
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${IMAGES}/${name}.png`, animations: "disabled", caret: "hide" });
  console.log(`docs/images/${name}.png`);
}

async function main() {
  await portFree();
  rmSync(DATA, { recursive: true, force: true });
  const env = { ...process.env, PT_DATA_DIR: DATA, PT_MARKET_DATA: "false" };
  const seeded = spawnSync(PYTHON, ["scripts/dev_data.py", "--development"], {
    cwd: BACKEND,
    env,
    stdio: "ignore",
  });
  if (seeded.status !== 0) {
    rmSync(DATA, { recursive: true, force: true });
    throw new Error(`dev_data.py failed (exit ${seeded.status})`);
  }

  const server = spawn(PYTHON, ["-m", "portfolio_tracker"], {
    cwd: BACKEND,
    env: {
      ...env,
      PT_HOST: "127.0.0.1",
      PT_PORT: String(PORT),
      PT_STATIC_DIR: `${FRONTEND}/dist`,
      PT_COOKIE_SECURE: "false",
    },
    stdio: "ignore",
  });
  console.log(`server started, PID ${server.pid}`);
  const browser = await chromium.launch();
  try {
    await ready(server);
    const context = await browser.newContext({
      baseURL: BASE,
      viewport: { width: 1440, height: 900 },
      locale: "en-GB",
      timezoneId: "UTC",
    });
    const page = await context.newPage();
    const api = page.request;
    const logged = await api.post("/api/v1/auth/login", { data: DEMO, headers: CSRF });
    if (!logged.ok()) throw new Error(`login failed (${logged.status()})`);
    // The demo investor works in Italian: English and the default light theme for the README.
    await api.patch("/api/v1/me", { data: { language: "en", theme: "moderno" }, headers: CSRF });
    // The demo's manual prices have a fixed date, long past, so every one would show "price not
    // updated". On this throwaway copy only, the same closes are entered again for yesterday
    // (UTC) through the public route; the demo data and the tests that rely on it stay as they
    // are (maintainer, README screenshots).
    const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
    const assets = await (await api.get("/api/v1/assets")).json();
    for (const asset of assets) {
      const prices = await (await api.get(`/api/v1/assets/${asset.id}/prices`)).json();
      const last = prices[0]; // one manual price per demo security
      if (!last) continue;
      const put = await api.put(`/api/v1/assets/${asset.id}/prices`, {
        data: { day: yesterday, close: last.close },
        headers: CSRF,
      });
      if (!put.ok()) throw new Error(`price of ${asset.name} refused (${put.status()})`);
    }

    // (a) The dashboard: the cards, where the gain comes from, the allocation charts.
    await page.goto("/");
    await page.getByRole("heading", { level: 1 }).waitFor();
    await page.locator(".chart svg").first().waitFor();
    await page.waitForLoadState("networkidle");
    await shoot(page, "dashboard");

    // (b) The holdings list.
    await page.goto("/holdings");
    await page.getByRole("heading", { name: "Holdings", level: 1 }).waitFor();
    await page.locator("table").first().waitFor();
    await page.waitForLoadState("networkidle");
    await shoot(page, "holdings");

    // (c) The import preview of the invented sales fixture, in the demo's euro account; the
    // four new securities get their type, so nothing blocks the confirmation.
    await page.goto("/import");
    await page.getByLabel("File to import").setInputFiles(FIXTURE);
    await page.getByRole("button", { name: "Continue to the preview" }).click();
    await page.getByLabel("Import into account").selectOption({ label: "Conto titoli" });
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page.getByRole("tablist", { name: "Lines by category" }).waitFor();
    const asset = (name) => page.locator(".import-asset", { hasText: name });
    for (const name of ["TEST ETF EUROPA ACC UCITS", "TEST ETF EURO DIST UCITS", "TEST ETF GLOBALE ACC UCITS"]) {
      await asset(name).getByLabel("Type").selectOption({ label: "ETF" });
    }
    await asset("TEST SALE SPA").getByLabel("Type").selectOption({ label: "Stock" });
    await page.waitForLoadState("networkidle");
    if ((await page.locator(".import-blocking").count()) > 0) {
      throw new Error("the import preview still has blocking lines");
    }
    // The count cards at the top, so the sale and its tick are clear of the sticky confirm bar.
    await page.locator(".import-asset").first().waitFor();
    await page.getByText("Transactions to import").evaluate((el) => {
      const top = el.closest(".card, section, div").getBoundingClientRect().top;
      window.scrollBy(0, top - 24);
    });
    await shoot(page, "import-preview");
  } finally {
    await browser.close();
    await stop(server);
    console.log(`server stopped, PID ${server.pid}`);
    rmSync(DATA, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
