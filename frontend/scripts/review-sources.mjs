// The price-sources page for the review screenshots (D-196), used by review-screenshots.mjs.
// Invented data only; no call leaves the machine.
//
// On its own throwaway fake market server (backend/tests/e2e_market_server.py: Yahoo Finance,
// OpenFIGI and Frankfurter answered by invented fakes) on :8719, in frontend/.data-review-
// sources/, stopped and deleted by the caller's finally, on a crash or an interruption: an
// Italian administrator per size and theme opens the page and takes its top and the whole page,
// an engine on and off, the order lists, the preferred exchanges and an exchange's error, the
// confirmation before a role is left without an engine (cancelled), "Aggiorna ora" and its
// answer, and the Administration card with its new link. Every change is undone before the next
// shot set. Once, the run refused while one is in progress (409 MARKET_DATA_RUNNING), for real:
// the administrator adds the gated invented ETF, whose job waits for the gate file, then the
// gate opens. Mocked in the browser, and named so: a paused engine with its last error (the
// fakes always work), and at 320 px a long unknown engine and long exchanges. The page with
// market data off is taken on the demo server, as its administrator.
import { spawn } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";

import { ADMIN, CSRF, FRONTEND, portFree } from "./demo-server.mjs";

export const SOURCES_DATA = `${FRONTEND}/.data-review-sources`; // ignored by git (.data*/)
export const SOURCES_PORT = 8719;
export const SOURCES_BASE = `http://127.0.0.1:${SOURCES_PORT}`;
// Invented credentials and ISIN (fake_isin(8701): the gated ETF of the launcher).
const OWNER = { username: "review-sources", password: "review sources password 0001" };
const WORLD_ISIN = "ZZ0000087013";
const GATE = `${SOURCES_DATA}/gate-ZWLD.MI`;
const PASSWORD = "review sources admin password 0001";
const BASE = "/api/v1/admin/market-data";

export const SOURCE_SIZES = {
  desktop: { width: 1440, height: 900 },
  tablet: { width: 820, height: 1180 },
  phone: { width: 390, height: 844 },
  narrow: { width: 320, height: 640 },
};

/** Starts the launcher of fake providers on a fresh folder (as e2e/serve.sh market). */
export async function startSources() {
  await portFree(SOURCES_PORT);
  rmSync(SOURCES_DATA, { recursive: true, force: true });
  mkdirSync(SOURCES_DATA, { recursive: true });
  const backend = `${FRONTEND}/../backend`;
  const server = spawn(`${backend}/.venv/bin/python`, ["tests/e2e_market_server.py"], {
    cwd: backend,
    env: {
      ...process.env,
      PT_DATA_DIR: SOURCES_DATA,
      PT_HOST: "127.0.0.1",
      PT_PORT: String(SOURCES_PORT),
      PT_STATIC_DIR: `${FRONTEND}/dist`,
      PT_MARKET_DATA: "false",
      PT_COOKIE_SECURE: "false",
      PT_REQUIRE_2FA: "off",
    },
    stdio: "ignore",
  });
  console.log(`price-sources server started, PID ${server.pid}`);
  const kill = () => {
    if (server.exitCode === null && server.signalCode === null) server.kill("SIGTERM");
  };
  process.once("exit", () => {
    kill();
    rmSync(SOURCES_DATA, { recursive: true, force: true });
  });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      kill();
      rmSync(SOURCES_DATA, { recursive: true, force: true });
      process.exit(130);
    });
  }
  return server;
}

/** The first administrator, then one Italian administrator in Moderno per tag. */
export async function sourcesAdministrators(browser, tags) {
  const context = await browser.newContext({ baseURL: SOURCES_BASE });
  const api = context.request;
  const created = await api.post("/api/v1/setup/admin", {
    data: { ...OWNER, first_name: "Sara", last_name: "Esempio", email: "review-sources@example.org", language: "it" },
    headers: CSRF,
  });
  if (created.status() !== 201) throw new Error(`price-sources administrator refused (${created.status()})`);
  const users = [];
  for (const tag of tags) {
    await api.post("/api/v1/auth/login", { data: OWNER, headers: CSRF });
    const username = `fonti-${tag}`;
    const made = await api.post("/api/v1/admin/users", {
      data: {
        username, language: "it", role: "ADMINISTRATOR", first_name: "Sara", last_name: "Esempio",
        email: `${username}@example.org`, password: OWNER.password,
      },
      headers: CSRF,
    }); // prettier-ignore
    if (!made.ok()) throw new Error(`administrator ${username} refused (${made.status()})`);
    const { temporary_password } = await made.json();
    await api.post("/api/v1/auth/logout", { headers: CSRF });
    await api.post("/api/v1/auth/login", { data: { username, password: temporary_password }, headers: CSRF });
    await api.post("/api/v1/me/password", {
      data: { current_password: temporary_password, new_password: PASSWORD },
      headers: CSRF,
    });
    await api.patch("/api/v1/me", { data: { theme: "moderno" }, headers: CSRF });
    await api.post("/api/v1/auth/logout", { headers: CSRF });
    users.push({ username, password: PASSWORD });
  }
  await context.close();
  return users;
}

/** A browser of the given size and appearance, in Italian, logged in as `user`. */
export async function sourcesSession(browser, user, size, appearance, base = SOURCES_BASE) {
  const context = await browser.newContext({
    baseURL: base,
    viewport: SOURCE_SIZES[size],
    hasTouch: size === "phone" || size === "narrow",
    colorScheme: appearance === "dark" ? "dark" : "light",
    locale: "it-IT",
    timezoneId: "UTC",
  });
  const page = await context.newPage();
  const logged = await page.request.post("/api/v1/auth/login", { data: user, headers: CSRF });
  if (!logged.ok()) throw new Error(`login of ${user.username} failed (${logged.status()})`);
  return { context, page };
}

async function openSources(page) {
  await page.goto("/admin/price-sources");
  await page.getByRole("heading", { name: "Fonti prezzi", level: 1 }).waitFor();
  await page.getByRole("table", { name: "Fonti" }).waitFor();
  await page.getByRole("heading", { name: "Tipo di titolo", level: 3 }).waitFor();
  await page.waitForLoadState("networkidle");
}

async function toSection(page, name, level = 2) {
  await page
    .getByRole("heading", { name, level, exact: true })
    .evaluate((heading) => heading.scrollIntoView({ block: "start" }));
  await page.evaluate(() => window.scrollBy(0, -12));
}

const said = (page) => page.locator("main [role=status]").first();

/** Until no run is in progress, twice in a row a second apart (at most a minute). */
async function idle(page) {
  let quiet = 0;
  for (let i = 0; i < 60 && quiet < 2; i++) {
    const status = await (await page.request.get(`${BASE}/status`)).json();
    quiet = status.running === null ? quiet + 1 : 0;
    if (quiet < 2) await page.waitForTimeout(1000);
  }
}

/** One size and theme on the fake server: every part of the page; each change undone. */
export async function sourcesShots(page, tag, shoot) {
  await openSources(page);
  await shoot(page, `${tag}-sources-top`);
  await shoot(page, `${tag}-sources-full`, true);

  // An engine on (as it starts), then off: its status, and "Spenta: saltata" in its orders.
  await toSection(page, "Fonti");
  await shoot(page, `${tag}-sources-engine-on`);
  await page.getByRole("switch", { name: "Attiva OpenFIGI" }).click();
  await said(page).getByText("OpenFIGI è spenta.").waitFor();
  await toSection(page, "Fonti");
  await shoot(page, `${tag}-sources-engine-off`);
  await toSection(page, "Ordine per uso");
  await shoot(page, `${tag}-sources-orders-engine-off`);
  await page.getByRole("switch", { name: "Attiva OpenFIGI" }).click();
  await said(page).getByText("OpenFIGI è attiva.").waitFor();

  // The orders, after a move (then moved back).
  await page.getByRole("button", { name: "Giù: OpenFIGI" }).click();
  await said(page).getByText(/^OpenFIGI: posizione 2 di 2/).waitFor();
  await toSection(page, "Ordine per uso");
  await shoot(page, `${tag}-sources-orders`);
  await page.getByRole("button", { name: "Su: OpenFIGI" }).click();
  await said(page).getByText(/^OpenFIGI: posizione 1 di 2/).waitFor();

  // The preferred exchanges: two added, then a repeat refused before sending.
  const field = page.getByLabel("Aggiungi una borsa");
  for (const code of ["GER", "MIL"]) {
    await field.fill(code);
    await field.press("Enter");
    await said(page).getByText(new RegExp(`^${code} · .+ aggiunta alle borse preferite`)).waitFor();
  }
  await toSection(page, "Borse preferite");
  await shoot(page, `${tag}-sources-exchanges`);
  await field.fill("ger");
  await field.press("Enter");
  await page.getByText("Questa borsa è già nell'elenco.").waitFor();
  await field.evaluate((input) => input.scrollIntoView({ block: "center" }));
  await shoot(page, `${tag}-sources-exchange-error`);
  await field.fill("");
  for (const name of ["GER · Xetra", "MIL · Milano"]) {
    await page.getByRole("button", { name: `Togli ${name}` }).click();
    await said(page).getByText(`${name} tolta dalle borse preferite.`).waitFor();
  }

  // The confirmation, cancelled: nothing is written.
  await page.getByRole("button", { name: "Togli Frankfurter" }).click();
  await page.getByRole("dialog", { name: "Lasciare senza fonte?" }).waitFor();
  await shoot(page, `${tag}-sources-confirm`);
  await page.getByRole("dialog").getByRole("button", { name: "Annulla" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached" });

  // "Aggiorna ora": its answer beside it.
  await idle(page);
  await page.getByRole("button", { name: "Aggiorna ora" }).click();
  await page.locator(".ps-run [role=status]").getByText(/^(Aggiornamento avviato|Un aggiornamento)/).waitFor();
  await toSection(page, "Aggiornamenti");
  await shoot(page, `${tag}-sources-run-started`);
  await idle(page);

  // Mocked in the browser: a paused engine with its last error (the fakes always work).
  await page.route("**/api/v1/admin/market-data/engines", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    const at = Date.now();
    body.engines = body.engines.map((engine) =>
      engine.name === "yahoo"
        ? {
            ...engine,
            status: "PAUSED",
            paused_until: new Date(at + 15 * 60_000).toISOString(),
            last_error_code: "RATE_LIMITED",
            last_error_at: new Date(at - 5 * 60_000).toISOString(),
          }
        : engine,
    );
    await route.fulfill({ response, json: body });
  });
  await openSources(page);
  const paused = page.getByText(/^In pausa fino a /);
  await paused.waitFor();
  await paused.evaluate((pill) => pill.closest("tr").scrollIntoView({ block: "center" }));
  await shoot(page, `${tag}-sources-mocked-status`);
  await page.unroute("**/api/v1/admin/market-data/engines");

  // The Administration card and its new link.
  await page.goto("/admin");
  await page.getByRole("link", { name: "Gestisci le fonti prezzi" }).waitFor();
  await page.waitForLoadState("networkidle");
  await toSection(page, "Fonti prezzi");
  await shoot(page, `${tag}-admin-sources-card`);
}

/** Once: "Aggiorna ora" while a run is in progress, for real. The administrator adds the gated
 * invented ETF (its linking job waits for the gate file), presses the button, then the gate
 * opens and the run ends. */
export async function runningShot(page, tag, shoot) {
  const added = await page.request.post("/api/v1/assets", {
    data: { name: "Esempio Mondo ETF", asset_type: "ETF", quote_currency: "EUR", quote_unit: "CURRENCY", isin: WORLD_ISIN },
    headers: CSRF,
  });
  if (!added.ok()) throw new Error(`the gated ETF refused (${added.status()})`);
  let running = false;
  for (let i = 0; i < 30 && !running; i++) {
    running = (await (await page.request.get(`${BASE}/status`)).json()).running !== null;
    if (!running) await page.waitForTimeout(500);
  }
  try {
    if (!running) {
      console.log("no run held by the gate: the shot of a run in progress is skipped");
      return;
    }
    await openSources(page);
    await page.getByText("Un aggiornamento è in corso", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Aggiorna ora" }).click();
    await page.locator(".ps-run [role=status]").getByText("Un aggiornamento è in corso: riprova quando è finito.").waitFor();
    await toSection(page, "Aggiornamenti");
    await shoot(page, `${tag}-sources-run-running`);
  } finally {
    writeFileSync(GATE, "open");
  }
  await idle(page);
}

/** At 320 px: long names (mocked in the browser), the orders and the exchanges, the dialog. */
export async function narrowSourcesShots(page, shoot) {
  await page.route("**/api/v1/admin/market-data/routes", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    body.routes.ETF_STOCK = [...body.routes.ETF_STOCK, "un-motore-di-una-versione-futura-con-un-nome-molto-lungo"];
    body.exchanges = ["Borsaconunnomelunghissimochenonsispezzamaiinnessunpuntodelrigo", "Una borsa con un nome lungo in parole, che va a capo", "GER"];
    await route.fulfill({ response, json: body });
  });
  await openSources(page);
  await shoot(page, "narrow-light-sources-top");
  await toSection(page, "Fonti");
  await shoot(page, "narrow-light-sources-engines");
  await toSection(page, "Ordine per uso");
  await shoot(page, "narrow-light-sources-orders-long-names-mocked");
  await toSection(page, "Borse preferite");
  await shoot(page, "narrow-light-sources-exchanges-long-names-mocked");
  await shoot(page, "narrow-light-sources-full-long-names-mocked", true);
  await page.getByRole("button", { name: "Togli Frankfurter" }).click();
  await page.getByRole("dialog", { name: "Lasciare senza fonte?" }).waitFor();
  await shoot(page, "narrow-light-sources-confirm");
  await page.getByRole("dialog").getByRole("button", { name: "Annulla" }).click();
}

/** On the demo server (market data off), as its administrator. */
export async function offShot(page, tag, shoot) {
  await openSources(page);
  await shoot(page, `${tag}-sources-market-off`);
}

export { ADMIN as DEMO_ADMIN };
