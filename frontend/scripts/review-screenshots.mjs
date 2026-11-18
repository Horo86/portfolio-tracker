// Screenshots for the maintainer's review of the phone and tablet layout (D-189); opt-in and
// local, never in CI, never committed. `npm run screenshots:review` from the frontend folder
// (it builds first); it needs the backend's environment and Playwright's Chromium, as
// `npm run screenshots`.
//
// It seeds the invented demo data in a throwaway folder, starts the app on :8716 with market
// data off, and takes each page as the demo investor and Administration as the demo
// administrator, in Italian, at 390x844 (phone) and 820x1180 (tablet), with the light Moderno
// theme and with dark, the "Altro" menu open too; plus the dashboard in dark at 844x390 (a
// phone on its side). The images go to frontend/.review-screenshots/ (ignored by git); the
// data folder is deleted at the end.
import { mkdirSync, rmSync } from "node:fs";

import { chromium } from "@playwright/test";

import { ADMIN, CSRF, DEMO, FRONTEND, freshPrices, ready, startDemo, stop } from "./demo-server.mjs";

const DATA = `${FRONTEND}/.data-review`; // ignored by git (.data*/)
const OUT = `${FRONTEND}/.review-screenshots`; // ignored by git
const PORT = 8716;
const BASE = `http://127.0.0.1:${PORT}`;

const SIZES = {
  phone: { width: 390, height: 844 },
  tablet: { width: 820, height: 1180 },
  landscape: { width: 844, height: 390 },
};
// The investor's pages; Administration is taken as the administrator.
const PAGES = [
  ["dashboard", "/"],
  ["holdings", "/holdings"],
  ["transactions", "/transactions"],
  ["import", "/import"],
  ["accounts", "/accounts"],
  ["settings", "/settings"],
  ["about", "/about"],
];

async function shoot(page, name) {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${OUT}/${name}.png`, animations: "disabled", caret: "hide" });
  console.log(`.review-screenshots/${name}.png`);
}

async function open(page, path) {
  await page.goto(path);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.waitForLoadState("networkidle");
}

async function openMenu(page) {
  await page.getByRole("button", { name: "Altro" }).click();
  await page.getByRole("dialog", { name: "Altro" }).waitFor();
  await page.waitForLoadState("networkidle");
}

/** A browser context of the given size and appearance, logged in as `user`. */
async function session(browser, user, size, appearance) {
  const context = await browser.newContext({
    baseURL: BASE,
    viewport: SIZES[size],
    hasTouch: size !== "tablet",
    colorScheme: appearance === "dark" ? "dark" : "light",
    locale: "it-IT",
    timezoneId: "UTC",
  });
  const page = await context.newPage();
  const logged = await page.request.post("/api/v1/auth/login", { data: user, headers: CSRF });
  if (!logged.ok()) throw new Error(`login of ${user.username} failed (${logged.status()})`);
  return { context, page };
}

async function main() {
  const server = await startDemo(DATA, PORT);
  const browser = await chromium.launch();
  try {
    await ready(server, BASE);
    rmSync(OUT, { recursive: true, force: true });
    mkdirSync(OUT, { recursive: true });

    // Both users with the default light theme, Moderno; the demo's prices dated yesterday.
    for (const user of [DEMO, ADMIN]) {
      const { context, page } = await session(browser, user, "tablet", "light");
      await page.request.patch("/api/v1/me", { data: { theme: "moderno" }, headers: CSRF });
      if (user === DEMO) await freshPrices(page.request);
      await context.close();
    }

    for (const size of ["phone", "tablet"]) {
      for (const appearance of ["light", "dark"]) {
        const tag = `${size}-${appearance}`;
        const demo = await session(browser, DEMO, size, appearance);
        for (const [name, path] of PAGES) {
          await open(demo.page, path);
          await shoot(demo.page, `${tag}-${name}`);
        }
        await open(demo.page, "/");
        await openMenu(demo.page);
        await shoot(demo.page, `${tag}-menu`);
        await demo.context.close();

        const admin = await session(browser, ADMIN, size, appearance);
        await open(admin.page, "/admin");
        await shoot(admin.page, `${tag}-admin`);
        await openMenu(admin.page);
        await shoot(admin.page, `${tag}-admin-menu`);
        await admin.context.close();
      }
    }

    // A phone on its side: how much height the strip and the bar leave.
    const side = await session(browser, DEMO, "landscape", "dark");
    await open(side.page, "/");
    await shoot(side.page, "landscape-dark-dashboard");
    await side.context.close();
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
