import { type APIRequestContext, expect, type Page, request } from "@playwright/test";

import { MARKET } from "../playwright.config";
import { CSRF, logIn, NEW_USER_NAMES, PASSWORD, type User } from "./helpers";
import { ensureMarketAdmin, MARKET_ADMIN } from "./marketUsers";

// The price-sources page (D-196) on the market server (:8712): its configuration is shared by
// every spec on that server, so the specs that change it put it back as they found it, and
// wait for the re-check runs their changes queued (D-134) to end before the next spec.

type Routes = { routes: Record<string, string[]>; exchanges: string[] };
type Engines = { engines: { name: string; enabled: boolean }[] };
export type Snapshot = { enabled: Record<string, boolean>; routes: Record<string, string[]>; exchanges: string[] };

const BASE = "/api/v1/admin/market-data";

async function asAdmin<T>(work: (api: APIRequestContext) => Promise<T>): Promise<T> {
  await ensureMarketAdmin();
  const api = await request.newContext({ baseURL: MARKET, extraHTTPHeaders: CSRF });
  const login = await api.post("/api/v1/auth/login", {
    data: { username: MARKET_ADMIN.username, password: MARKET_ADMIN.password },
  });
  expect(login.ok(), "login as the market administrator").toBe(true);
  try {
    return await work(api);
  } finally {
    await api.post("/api/v1/auth/logout");
    await api.dispose();
  }
}

/** The configuration as it is now. */
export function snapshot(): Promise<Snapshot> {
  return asAdmin(async (api) => {
    const engines = (await (await api.get(`${BASE}/engines`)).json()) as Engines;
    const routes = (await (await api.get(`${BASE}/routes`)).json()) as Routes;
    return {
      enabled: Object.fromEntries(engines.engines.map((e) => [e.name, e.enabled])),
      routes: routes.routes,
      exchanges: routes.exchanges,
    };
  });
}

/** Puts `saved` back (a write with no change does nothing, D-188 (7)), then waits until no
 * run is in progress. */
export function restore(saved: Snapshot): Promise<void> {
  return asAdmin(async (api) => {
    for (const [name, enabled] of Object.entries(saved.enabled)) {
      const out = await api.patch(`${BASE}/engines/${name}`, { data: { enabled } });
      expect(out.ok(), `restore ${name}`).toBe(true);
    }
    for (const [role, engines] of Object.entries(saved.routes)) {
      const out = await api.put(`${BASE}/routes/${role}`, { data: { engines } });
      expect(out.ok(), `restore ${role}`).toBe(true);
    }
    const out = await api.put(`${BASE}/exchanges`, { data: { exchanges: saved.exchanges } });
    expect(out.ok(), "restore the exchanges").toBe(true);
    await idle(api);
  });
}

/** Until the status shows no run twice in a row, a second apart (at most a minute). */
async function idle(api: APIRequestContext): Promise<void> {
  let quiet = 0;
  for (let i = 0; i < 60 && quiet < 2; i++) {
    const status = (await (await api.get(`${BASE}/status`)).json()) as { running: string | null };
    quiet = status.running === null ? quiet + 1 : 0;
    if (quiet < 2) await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  expect(quiet, "no market data run in progress").toBe(2);
}

/** A long engine name this version does not know and a long exchange, put into the page's
 * answer only (nothing stored): the reflow cases at 320 px (D-196). */
export const LONG_ENGINE = "an-engine-from-a-later-version-with-a-very-long-name";
export const LONG_EXCHANGE = "Borsaconunnomelunghissimochenonsispezzamaiinnessunpuntodelrigo"; // 62
export const LONG_SPACED = "An exchange with a long name in words, that wraps";

export async function longNames(page: Page): Promise<void> {
  await page.route("**/api/v1/admin/market-data/routes", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = (await response.json()) as Routes;
    body.routes.ETF_STOCK = [...body.routes.ETF_STOCK, LONG_ENGINE];
    body.exchanges = [LONG_EXCHANGE, LONG_SPACED, "GER"];
    await route.fulfill({ response, json: body });
  });
}

let serial = 0;

/** A new administrator on the market server (the market administrator acts, D-175 (6): its
 * own password, no 2FA there), so a theme or a language set here never reaches other specs. */
export async function marketAdministrator(prefix: string, language = "en"): Promise<User> {
  const username = `${prefix}-${Date.now().toString(36)}${serial++}`;
  const temporary = await asAdmin(async (api) => {
    const created = await api.post("/api/v1/admin/users", {
      data: {
        username,
        language,
        role: "ADMINISTRATOR",
        ...NEW_USER_NAMES,
        email: `${username}@example.org`,
        password: MARKET_ADMIN.password,
      },
    });
    expect(created.ok(), "create a market administrator").toBe(true);
    return ((await created.json()) as { temporary_password: string }).temporary_password;
  });
  const api = await request.newContext({ baseURL: MARKET, extraHTTPHeaders: CSRF });
  await api.post("/api/v1/auth/login", { data: { username, password: temporary } });
  const changed = await api.post("/api/v1/me/password", {
    data: { current_password: temporary, new_password: PASSWORD },
  });
  expect(changed.ok(), "replace the temporary password").toBe(true);
  await api.post("/api/v1/auth/logout");
  await api.dispose();
  return { username, password: PASSWORD, ...NEW_USER_NAMES };
}

/** Logs in as a new market administrator and opens the page, every card loaded. */
export async function openPriceSources(page: Page, prefix: string): Promise<User> {
  const admin = await marketAdministrator(prefix);
  await logIn(page, admin);
  await page.goto("/admin/price-sources");
  await expect(page.getByRole("heading", { name: "Price sources", level: 1 })).toBeVisible();
  await expect(page.getByRole("table", { name: "Sources" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Security type", level: 3 })).toBeVisible();
  await expect(page.getByRole("button", { name: "Update now" })).toBeVisible();
  return admin;
}

/** The size of each visible row button of the order and exchange lists, and whether it shows
 * its word or its arrow; the accessible names stay the same on every screen. */
export async function rowButtons(page: Page) {
  return page.locator(".ps-buttons .ps-button").evaluateAll((list) =>
    list.map((button) => {
      const box = button.getBoundingClientRect();
      const word = button.querySelector(".ps-word") as HTMLElement;
      return {
        name: button.getAttribute("aria-label"),
        width: Math.round(box.width),
        height: Math.round(box.height),
        word: word.offsetParent !== null,
      };
    }),
  );
}

/** The cells of an engine's card: [top, left, width] of each, and the row's width. */
export async function engineCard(page: Page, name: string) {
  return page
    .getByRole("row", { name: new RegExp(`^${name}`) })
    .evaluate((row) => {
      const box = (el: Element) => el.getBoundingClientRect();
      return {
        display: getComputedStyle(row).display,
        width: Math.round(box(row).width),
        cells: [...row.children].map((cell) => [
          Math.round(box(cell).top),
          Math.round(box(cell).left),
          Math.round(box(cell).width),
        ]),
      };
    });
}

/** The engines' answer with a paused engine and its last error (invented values, in the
 * browser only): the fakes of the market server always work (D-196's review screenshot and
 * the axe walks). */
export async function mockedStatus(page: Page): Promise<void> {
  await page.route("**/api/v1/admin/market-data/engines", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = (await response.json()) as { engines: Record<string, unknown>[] };
    const soon = new Date(Date.now() + 15 * 60_000).toISOString();
    const before = new Date(Date.now() - 5 * 60_000).toISOString();
    body.engines = body.engines.map((engine) =>
      engine.name === "yahoo"
        ? {
            ...engine,
            status: "PAUSED",
            paused_until: soon,
            last_error_code: "RATE_LIMITED",
            last_error_at: before,
          }
        : engine,
    );
    await route.fulfill({ response, json: body });
  });
}
