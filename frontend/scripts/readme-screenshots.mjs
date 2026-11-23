// The README's screenshots (docs/images/), made again on demand; never in CI. Opt-in and local:
// `npm run screenshots` from the frontend folder (it builds first). It needs the backend's
// environment (`cd backend && uv sync`) and Playwright's Chromium (`npx playwright install
// chromium`, once, as for the end-to-end tests).
//
// It fills a throwaway folder with the invented demo data (backend/scripts/dev_data.py:
// `ZZ` ISINs, made-up names and amounts), starts the app on it with market data off, logs in
// as the demo investor, switches to English and the default light theme, and takes five
// screenshots: the dashboard and the holdings list at 1440x900, and three phone shots at
// 390x844 (device scale 2, touch, like e2e/phone.spec.ts): the dashboard's cards in light, the
// allocation donut in dark (the browser asks for the dark colour scheme and the app's "same as
// the device" shows it) and the holdings as cards. Then it stops the server it started and
// deletes the folder. No real data is used anywhere.
import { rmSync } from "node:fs";

import { chromium } from "@playwright/test";

import { CSRF, DEMO, FRONTEND, freshPrices, ready, startDemo, stop } from "./demo-server.mjs";

const DATA = `${FRONTEND}/.data-screenshots`; // ignored by git (.data*/)
const IMAGES = `${FRONTEND}/../docs/images`;
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

    // The phone shots: each in its own context (own cookies and colour scheme), one viewport
    // each, scrolled by a known heading so every run is the same.
    const phone = async (scheme, name, open) => {
      const ctx = await browser.newContext({
        baseURL: BASE,
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 2,
        isMobile: true,
        hasTouch: true,
        colorScheme: scheme,
        locale: "en-GB",
        timezoneId: "UTC",
      });
      try {
        const mobile = await ctx.newPage();
        const res = await mobile.request.post("/api/v1/auth/login", { data: DEMO, headers: CSRF });
        if (!res.ok()) throw new Error(`login failed (${res.status()})`);
        await open(mobile);
        await mobile.waitForLoadState("networkidle");
        if (scheme === "dark" && (await mobile.locator("html").getAttribute("data-theme")) !== "scuro") {
          throw new Error("the dark shot does not show the dark theme");
        }
        await shoot(mobile, name);
      } finally {
        await ctx.close();
      }
    };
    const top = (selector, gap) => (page) =>
      page.locator(selector).first().evaluate((el, gap) => {
        window.scrollBy(0, el.getBoundingClientRect().top - gap);
      }, gap);

    await phone("light", "phone-dashboard", async (p) => {
      await p.goto("/");
      await p.getByRole("heading", { level: 1 }).waitFor();
      await p.locator(".chart svg").first().waitFor();
      // Just past the "automatic prices are off" notice: the three figure cards.
      await top("#dashboard-total", 40)(p);
    });
    await phone("dark", "phone-dashboard-dark", async (p) => {
      await p.goto("/");
      await p.locator(".chart svg").first().waitFor();
      await top("#dashboard-allocation", 72)(p);
    });
    await phone("light", "phone-holdings", async (p) => {
      await p.goto("/holdings");
      await p.locator(".holding-row").first().waitFor();
      await top(".table-card", 16)(p);
    });
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
