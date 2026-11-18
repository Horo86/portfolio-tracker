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
import { rmSync } from "node:fs";

import { chromium } from "@playwright/test";

import { CSRF, DEMO, FRONTEND, freshPrices, ready, startDemo, stop } from "./demo-server.mjs";

const DATA = `${FRONTEND}/.data-screenshots`; // ignored by git (.data*/)
const IMAGES = `${FRONTEND}/../docs/images`;
const FIXTURE = `${FRONTEND}/../tests/fixtures/directa/directa_synthetic_sales_2025.xlsx`;
const PORT = 8714;
const BASE = `http://127.0.0.1:${PORT}`;

async function shoot(page, name) {
  // No animation half way, no caret, the fonts loaded.
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${IMAGES}/${name}.png`, animations: "disabled", caret: "hide" });
  console.log(`docs/images/${name}.png`);
}

async function main() {
  const server = await startDemo(DATA, PORT);
  const browser = await chromium.launch();
  try {
    await ready(server, BASE);
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
    // updated": on this throwaway copy only, they are entered again for yesterday.
    await freshPrices(api);

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
