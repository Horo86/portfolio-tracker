import { type ChildProcess, spawn } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { createServer } from "node:net";

import { expect, type Locator, type Page, test } from "./fixtures";

import { WALKTHROUGH } from "../playwright.walkthrough.config";
import { formatDay } from "../src/format/date";
import { expectAccessible } from "./helpers";

// The M5 acceptance path (private/plans/m5-acceptance.md, parts A to I) as one scenario, with
// the same figures and texts, in Italian, on a server of its own that this spec starts, stops
// and restarts (D-159). Invented names and the public invented fixtures only. Each step says
// its number; a step or part left out says why (a human, another browser, or another check).

const FRONTEND = process.cwd(); // `npm run e2e:walkthrough` runs in the frontend folder
const BACKEND = `${FRONTEND}/../backend`;
const DATA = `${FRONTEND}/.data-e2e-walkthrough`; // ignored by git (.data*/)
const PORT = WALKTHROUGH.split(":").at(-1)!;
const FIXTURES = `${FRONTEND}/../tests/fixtures/directa`;
const ADMIN = { username: "collaudo", password: "collaudo-m5-usa-e-getta" };

let server: ChildProcess | null = null;

/** Fails at once when something already listens on the walkthrough's port: the steps would
 * run against that server instead of ours (review R-12). */
async function portFree() {
  await new Promise<void>((resolve, reject) => {
    const probe = createServer();
    probe.once("error", (error) =>
      reject(new Error(`Port ${PORT} is in use: stop what runs there first (${error.message}).`)),
    );
    probe.listen(Number(PORT), "127.0.0.1", () => probe.close(() => resolve()));
  });
}

/** Starts the app on the walkthrough's data folder, market data off, as e2e/serve.sh does;
 * fails at once if it exits before it answers (review R-12). */
async function start(page: Page) {
  await portFree();
  const child = spawn(`${BACKEND}/.venv/bin/python`, ["-m", "portfolio_tracker"], {
    cwd: BACKEND,
    env: {
      ...process.env,
      PT_DATA_DIR: DATA,
      PT_HOST: "127.0.0.1",
      PT_PORT: PORT,
      PT_STATIC_DIR: `${FRONTEND}/dist`,
      PT_MARKET_DATA: "false",
      PT_COOKIE_SECURE: "false",
    },
    stdio: "ignore",
  });
  server = child;
  let ended = "";
  const exited = new Promise<"exited">((resolve) =>
    child.once("exit", (code, signal) => {
      ended = `code ${code}, signal ${signal}`;
      resolve("exited");
    }),
  );
  const ready = expect
    .poll(async () => (await page.request.get("/api/health").catch(() => null))?.ok() ?? false, {
      timeout: 60_000,
    })
    .toBe(true)
    .then(() => "ready" as const);
  if ((await Promise.race([ready, exited])) === "exited") {
    server = null;
    throw new Error(
      `The walkthrough's server exited (${ended}) before it answered.`,
    );
  }
}

/** Stops it (SIGTERM, a clean stop) and waits until it has gone. */
async function stop() {
  const running = server;
  server = null;
  if (!running || running.exitCode !== null) return;
  const gone = new Promise((resolve) => running.once("exit", resolve));
  running.kill("SIGTERM");
  await gone;
}

test.afterAll(stop);

// --- Small helpers ----------------------------------------------------------------------

const iso = (date: Date) => date.toISOString().slice(0, 10);
const TODAY = iso(new Date());
const TOMORROW = iso(new Date(Date.now() + 86_400_000));
/** A day as the date fields take it (gg/mm/aaaa). */
const typed = (day: string) => day.split("-").reverse().join("/");
/** A day as the pages write it ("31 ott 2026"). */
const shown = (day: string) => formatDay(day, "it");

async function menu(page: Page, name: string) {
  const link = page
    .getByRole("navigation", { name: "Menu principale" })
    .getByRole("link", { name, exact: true });
  await link.click();
  // The page has changed only when its link is the current one: the previous page's h1 would
  // satisfy a wait for "a heading" before the navigation (6 dashboard tables were still there
  // when step 61 looked for the transactions table, CI run 41478760507).
  await expect(link).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
}

async function view(page: Page, account: string) {
  await page.getByLabel("Conto in vista").selectOption({ label: account });
}

/** Presses Tab until `target` has the focus (fails if it never does). */
async function tabTo(page: Page, target: Locator, max = 80) {
  for (let i = 0; i < max; i++) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`Tab never reached ${target}`);
}

async function typeInto(page: Page, target: Locator, text: string) {
  await tabTo(page, target);
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type(text);
}

const status = (page: Page, text: string | RegExp) =>
  page.getByRole("status").filter({ hasText: text });

async function accountCard(page: Page, name: string) {
  await menu(page, "Conti");
  return page.getByRole("region", { name });
}

// --- The scenario -----------------------------------------------------------------------

test("the M5 acceptance path, parts A to I", async ({ page }) => {
  test.setTimeout(10 * 60_000);
  rmSync(DATA, { recursive: true, force: true });
  mkdirSync(DATA, { recursive: true });
  await start(page);

  await test.step("A. first start and first access (1-4)", async () => {
    // 1: health answers; the log line about market data is the container's (not checked here).
    const health = await page.request.get("/api/health");
    expect(await health.json()).toMatchObject({ status: "ok" });

    // 2
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Benvenuto" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Ripristina da un backup" })).toBeDisabled();
    await expect(
      page.getByText("Il ripristino da un backup sarà disponibile in una versione successiva."),
    ).toBeVisible();
    await page.getByRole("button", { name: "Inizia da zero" }).click();
    await page.getByLabel("Nome utente").fill(ADMIN.username);
    await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);
    await page.getByLabel("Ripeti la password").fill(ADMIN.password);
    await page.getByRole("button", { name: "Continua" }).click();
    await page.getByRole("radio", { name: "Italiano" }).check();
    await page.getByRole("button", { name: "Crea l'amministratore" }).click();

    // 3
    const greeting = "Ciao collaudo, ecco i tuoi investimenti";
    await expect(page.getByRole("heading", { name: greeting, level: 1 })).toBeVisible();
    await expect(
      page.getByText(
        "La dashboard è vuota perché non hai ancora nessun conto. In tre passi sei operativo:",
      ),
    ).toBeVisible();
    for (const step of [
      "Crea un conto (nome e valuta).",
      "Aggiungi i titoli che già possiedi, oppure importa il file del broker.",
      "Guarda la dashboard.",
    ]) {
      await expect(page.getByRole("listitem").filter({ hasText: step })).toBeVisible();
    }
    // A link drawn as a button, as on every state card (screen 19).
    await page.getByRole("link", { name: "Crea il primo conto" }).click();
    const dialog = page.getByRole("dialog", { name: "Nuovo conto" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Annulla" }).click();
    await expect(page.getByRole("heading", { name: "Ancora nessun conto" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Crea il primo conto" })).toBeVisible();

    // 4: each theme switches and axe passes (their look needs a human); language and reload.
    await menu(page, "Impostazioni");
    for (const [label, theme] of [
      ["Sobrio", "sobrio"],
      ["Moderno", "moderno"],
      ["Ambra", "ambra"],
      ["Grafite", "grafite"],
    ] as const) {
      await page.locator("label", { hasText: label }).click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expectAccessible(page, `settings, ${theme}`);
    }
    await page.getByLabel("Lingua").selectOption("en");
    await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
    await page.getByLabel("Language").selectOption("it");
    await expect(page.getByRole("heading", { name: "Impostazioni", level: 1 })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: "Impostazioni", level: 1 })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "grafite");
    await expect(page.locator("html")).toHaveAttribute("lang", "it");
    await expect(page.getByText("Questo dispositivo")).toBeVisible();
    await expect(page.getByRole("button", { name: "Esci dagli altri dispositivi" })).toBeVisible();
    await page.getByRole("button", { name: "Cambia password" }).click();
    const change = page.getByRole("dialog");
    await change.getByLabel("Password attuale").fill(ADMIN.password);
    await change.getByLabel("Nuova password", { exact: true }).fill("corta");
    await change.getByLabel("Ripeti la nuova password").fill("corta");
    await change.getByRole("button", { name: "Cambia password" }).click();
    await expect(change.getByText("La password deve avere almeno 12 caratteri.")).toBeVisible();
    await change.getByRole("button", { name: "Annulla" }).click();
    await page.getByRole("button", { name: "Esci", exact: true }).click();
    await page.getByLabel("Nome utente").fill(ADMIN.username);
    await page.getByLabel("Password").fill("password-sbagliata");
    await page.getByRole("button", { name: "Accedi" }).click();
    await expect(page.getByText("Nome utente o password errati.")).toBeVisible();
    await page.getByLabel("Password").fill(ADMIN.password);
    await page.getByRole("button", { name: "Accedi" }).click();
    await expect(page.getByRole("heading", { name: greeting, level: 1 })).toBeVisible();
  });

  await test.step("B. accounts (5-6)", async () => {
    // 5
    await menu(page, "Conti");
    await page.getByRole("button", { name: "Crea il primo conto" }).click();
    let dialog = page.getByRole("dialog", { name: "Nuovo conto" });
    await dialog.getByLabel("Nome").fill("Conto titoli");
    await dialog.getByRole("button", { name: "Crea conto" }).click();
    await page.getByRole("button", { name: "Nuovo conto" }).click();
    dialog = page.getByRole("dialog", { name: "Nuovo conto" });
    await dialog.getByLabel("Nome").fill("Conto estero");
    await dialog.getByLabel("Valuta").selectOption("USD");
    await dialog.getByRole("button", { name: "Crea conto" }).click();
    for (const name of ["Conto titoli", "Conto estero"]) {
      const card = page.getByRole("region", { name });
      await expect(card).toContainText("Cassa");
      await expect(card).not.toContainText("Liquidità");
    }

    // 6
    await page.getByRole("region", { name: "Conto titoli" }).getByRole("link", { name: "Apri" }).click();
    await expect(page).toHaveURL(/\?account=/);
    await page.reload();
    await expect(page).toHaveURL(/\?account=/);
    await expect(page.getByLabel("Conto in vista")).toHaveValue(/[0-9a-f-]{36}/);
  });


  const summary = page.getByRole("complementary", { name: "Riepilogo" });
  const cancel = page.getByRole("link", { name: "Annulla", exact: true });
  const main_ = page.getByRole("main");

  await test.step("C. transactions, dates and keyboard (7-20)", async () => {
    // 7: keyboard only, until the deposit is saved.
    const nav = page.getByRole("navigation", { name: "Menu principale" });
    await tabTo(page, nav.getByRole("link", { name: "Transazioni" }));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Transazioni", level: 1 })).toBeVisible();
    await tabTo(page, page.getByRole("link", { name: "Aggiungi transazione" }));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Nuova transazione", level: 1 })).toBeFocused();
    await tabTo(page, page.getByRole("radio", { name: "Acquisto" }));
    const deposit = page.getByRole("radio", { name: "Versamento" });
    for (let i = 0; i < 8 && !(await deposit.isChecked()); i++) {
      await page.keyboard.press("ArrowRight");
    }
    await expect(deposit).toBeChecked();
    await typeInto(page, page.getByLabel("Data", { exact: true }), "02/01/2025");
    await typeInto(page, page.getByLabel("Importo (EUR)"), "10000");
    await expect(summary).toContainText(/Cassa dopo\s*10\.000,00\s€/);
    await tabTo(page, page.getByRole("button", { name: "Salva", exact: true }));
    await page.keyboard.press("Enter");
    await expect(status(page, "Transazione salvata.")).toBeVisible();
    await expect(page.getByRole("row", { name: /Versamento/ })).toBeVisible();

    // 8
    await page.getByRole("link", { name: "Aggiungi transazione" }).click();
    await page.getByRole("button", { name: "Nuovo titolo…" }).click();
    let dialog = page.getByRole("dialog", { name: "Nuovo titolo" });
    await dialog.getByLabel("Nome").fill("Esempio Mondo ETF");
    await dialog.getByLabel("Tipo").selectOption({ label: "ETF" });
    await expect(dialog.getByLabel("Valuta")).toHaveValue("EUR");
    await dialog.getByRole("button", { name: "Crea titolo" }).click();
    await expect(page.getByText("Titolo «Esempio Mondo ETF» creato e scelto.")).toBeVisible();
    await page.getByLabel("Data", { exact: true }).fill("10/01/2025");
    await page.getByLabel("Quantità").fill("50");
    await page.getByLabel("Prezzo unitario (EUR)").fill("100");
    await page.getByLabel("Commissioni (EUR)").fill("2,50");
    await expect(summary).toContainText(/Controvalore.*5\.000,00\s€/);
    await expect(summary).toContainText(/Commissioni\s*2,50\s€/);
    await expect(summary).toContainText(/Costo totale\s*5\.002,50\s€/);
    await expect(summary).toContainText(/Cassa dopo\s*4\.997,50\s€/);
    await expect(summary).toContainText(/Quantità dopo\s*50/);
    await expect(summary).toContainText(/Nuovo prezzo medio di carico\s*100,05\s€/);
    await expect(summary).not.toContainText("PMC");
    await page.getByRole("button", { name: "Salva", exact: true }).click();
    await expect(status(page, "Transazione salvata.")).toBeVisible();

    // 9: a sale of more than is held is refused.
    await page.getByRole("link", { name: "Aggiungi transazione" }).click();
    await page.getByRole("radio", { name: "Vendita" }).check();
    await page.getByLabel("Titolo", { exact: true }).selectOption({ label: "Esempio Mondo ETF · EUR" });
    await page.getByLabel("Data", { exact: true }).fill("02/06/2025");
    await page.getByLabel("Quantità").fill("60");
    await page.getByLabel("Prezzo unitario (EUR)").fill("110");
    await expect(summary).toContainText(/prima di questa transazione avresti 50, e ne resterebbero .10/);
    await page.getByRole("button", { name: "Salva", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "Questa modifica non si può salvare" });
    await expect(dialog.getByRole("link", { name: "Aggiungi titoli che già possiedo" })).toBeVisible();
    await dialog.getByRole("button", { name: "Torna alla modifica" }).click();

    // 10
    await page.getByLabel("Quantità").fill("10");
    await page.getByLabel("Commissioni (EUR)").fill("2");
    await expect(summary).toContainText("1.098,00 €");
    await expect(summary).toContainText(/Cassa dopo\s*6\.095,50\s€/);
    await expect(summary).toContainText(/Quantità dopo\s*40/);
    await page.getByRole("button", { name: "Salva", exact: true }).click();
    await expect(status(page, "Transazione salvata.")).toBeVisible();

    // 11: the focus goes to the first missing field; each error read once needs a screen reader.
    await page.getByRole("link", { name: "Aggiungi transazione" }).click();
    await page.getByRole("button", { name: "Salva", exact: true }).click();
    await expect(page.getByText("Compila questo campo.").first()).toBeVisible();
    await expect(page.locator(":focus")).toHaveAttribute("aria-invalid", "true");
    await cancel.click();

    // 12: dates as gg/mm/aaaa; nothing is saved.
    await page.getByRole("link", { name: "Aggiungi transazione" }).click();
    const date = page.getByLabel("Data", { exact: true });
    await expect(date).toHaveValue(typed(TODAY));
    await date.fill("");
    await expect(date).toHaveAttribute("placeholder", "gg/mm/aaaa");
    await date.fill("31/02/2026");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Scegli dal calendario: Data", exact: true })).toBeFocused();
    await expect(page.getByText("Questa data non esiste.")).toBeVisible();
    await page.keyboard.press("Shift+Tab");
    await expect(date).toBeFocused();
    for (const good of ["5.3.2026", "5-3-2026"]) {
      await date.fill(good);
      await page.keyboard.press("Tab");
      await expect(page.getByText(/Questa data non esiste|Scrivi la data come/)).toHaveCount(0);
    }
    for (const short of ["5/3/26", "5.3.26"]) {
      await date.fill(short);
      await page.keyboard.press("Tab");
      await expect(page.getByText("Scrivi la data come gg/mm/aaaa.")).toBeVisible();
    }
    await date.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Compila questo campo.").first()).toBeVisible();
    await expect(page).toHaveURL(/\/transactions\/new/);
    const valueDate = page.getByLabel("Data valuta (facoltativa)", { exact: true });
    await valueDate.fill("1-4-2026");
    await page.keyboard.press("Tab");
    await expect(page.getByText("Scrivi la data come gg/mm/aaaa.")).toHaveCount(1); // Data's own
    // 13: the calendar button is there; picking a day in the browser's own calendar, and
    // Firefox and Safari, need a human (D-159: the maintainer's check before M7).
    await expect(page.getByRole("button", { name: "Scegli dal calendario: Data", exact: true })).toBeVisible();
    await cancel.click();

    // 14
    await expect(page.getByText("Vista: Conto titoli · 3 movimenti")).toBeVisible();
    await page.getByRole("link", { name: "Modifica: Versamento, 2 gen 2025" }).click();
    await page.getByLabel("Note (facoltative)").fill("Nota di collaudo");
    await page.getByRole("button", { name: "Salva", exact: true }).click();
    await expect(status(page, "Modifiche salvate.")).toBeVisible();
    await page.getByRole("link", { name: "Modifica: Versamento, 2 gen 2025" }).click();
    await page.getByRole("button", { name: "Elimina…" }).click();
    dialog = page.getByRole("dialog", { name: "Eliminare questa transazione?" });
    await expect(dialog).toContainText("Dopo l'eliminazione:");
    await dialog.getByRole("button", { name: "Annulla" }).click();
    await cancel.click();
    await page.getByRole("checkbox", { name: "Seleziona: Versamento, 2 gen 2025" }).check();
    await page.getByRole("button", { name: "Modifica selezionate" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Annulla" }).click();
    await page.getByRole("button", { name: "Elimina selezionate" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Annulla" }).click();
    await page.getByRole("checkbox", { name: "Seleziona: Versamento, 2 gen 2025" }).uncheck();

    // 15: filters, and "from" never after "to".
    const from = page.getByLabel("Dal", { exact: true });
    const to = page.getByLabel("Al", { exact: true });
    await from.fill("1/1/2025");
    await page.keyboard.press("Tab");
    await expect(page).toHaveURL(/from=2025-01-01/);
    await from.fill("1/1/25");
    await page.keyboard.press("Tab");
    await expect(page.getByText("Scrivi la data come gg/mm/aaaa.")).toBeVisible();
    await expect(page).toHaveURL(/from=2025-01-01/);
    await from.fill("01/02/2025");
    await to.fill("01/01/2025");
    await page.keyboard.press("Tab");
    await expect(page.getByText("La data di inizio non può essere dopo quella di fine.")).toBeVisible();
    await expect(page.getByRole("table")).toHaveCount(0);
    await page.getByRole("button", { name: "Azzera i filtri" }).click();
    await expect(from).toHaveValue("");
    await expect(to).toHaveValue("");

    // 16: no result (screen 19).
    await page.getByLabel("Tipo", { exact: true }).selectOption({ label: "Dividendo" });
    await expect(page.getByRole("heading", { name: "Nessun risultato" })).toBeVisible();
    await expect(
      page.getByText("Nessuna transazione corrisponde ai filtri scelti (tipo: Dividendo)."),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Azzera i filtri" })).toHaveCount(1);
    await from.fill("01/01/2025");
    await page.keyboard.press("Tab");
    await expect(page.getByText("(tipo: Dividendo, dal: 1 gen 2025)", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "Azzera i filtri" }).click();
    await expect(page.getByRole("row", { name: /Versamento/ })).toBeVisible();

    // 17
    await view(page, "Conto estero");
    await expect(page.getByRole("heading", { name: "Ancora nessuna transazione" })).toBeVisible();
    await expect(
      page.getByText("Le transazioni che aggiungi compaiono qui, dalla più recente."),
    ).toBeVisible();
    await view(page, "Conto titoli");

    // 18
    await expect(await accountCard(page, "Conto titoli")).toContainText("6.095,50 €");

    // 19: "Salva e aggiungi un'altra", then the deposit of 1,00 € deleted.
    await menu(page, "Transazioni");
    await page.getByRole("link", { name: "Aggiungi transazione" }).click();
    await page.getByRole("radio", { name: "Versamento" }).check();
    await page.getByLabel("Importo (EUR)").fill("1");
    const again = page.getByRole("button", { name: "Salva e aggiungi un'altra" });
    await again.click();
    await expect(status(page, "Transazione salvata.")).toBeVisible();
    await expect(again).toBeFocused();
    await expect(page.getByRole("radio", { name: "Versamento" })).toBeChecked();
    await expect(page.getByLabel("Data", { exact: true })).toHaveValue(typed(TODAY));
    await cancel.click();
    await page.getByRole("checkbox", { name: `Seleziona: Versamento, ${shown(TODAY)}` }).check();
    await page.getByRole("button", { name: "Elimina selezionate" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Elimina 1 transazione" }).click();
    await expect(status(page, "1 transazione eliminata.")).toBeVisible();
    await expect(await accountCard(page, "Conto titoli")).toContainText("6.095,50 €");

    // 20: the date field in English.
    await menu(page, "Impostazioni");
    await page.getByLabel("Lingua").selectOption("en");
    await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
    await page
      .getByRole("navigation", { name: "Main menu" })
      .getByRole("link", { name: "Transactions", exact: true })
      .click();
    await page.getByRole("link", { name: "Add transaction" }).click();
    const dateEn = page.getByLabel("Date", { exact: true });
    await dateEn.fill("");
    await expect(dateEn).toHaveAttribute("placeholder", "dd/mm/yyyy");
    await dateEn.fill("31/02/2026");
    await page.keyboard.press("Tab");
    await expect(page.getByText("This date does not exist.")).toBeVisible();
    await dateEn.fill("5/3/26");
    await page.keyboard.press("Tab");
    await expect(page.getByText("Enter the date as dd/mm/yyyy.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Pick from the calendar: Date", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Cancel", exact: true }).click();
    await page
      .getByRole("navigation", { name: "Main menu" })
      .getByRole("link", { name: "Settings", exact: true })
      .click();
    await page.getByLabel("Language").selectOption("it");
    await expect(page.getByRole("heading", { name: "Impostazioni", level: 1 })).toBeVisible();
  });

  const card = (label: string) => page.locator(".import-count", { hasText: label }).locator("dd").first();
  const confirmImport = page.getByRole("button", { name: /^Conferma/ });

  await test.step("D. import (21-25)", async () => {
    // 21
    await menu(page, "Conti");
    await page.getByRole("button", { name: "Nuovo conto" }).click();
    const dialog = page.getByRole("dialog", { name: "Nuovo conto" });
    await dialog.getByLabel("Nome").fill("Conto Directa");
    await dialog.getByRole("button", { name: "Crea conto" }).click();
    await expect(page.getByRole("region", { name: "Conto Directa" })).toBeVisible();

    // 22: a public text file in neither format (screen 19).
    await menu(page, "Importa");
    await page.getByLabel("File da importare").setInputFiles(`${FIXTURES}/README.md`);
    await page.getByRole("button", { name: "Continua all'anteprima" }).click();
    await expect(page.getByRole("heading", { name: "File non riconosciuto" })).toBeVisible();
    await expect(
      page.getByText(
        "Il file «README.md» non ha il formato Directa né quello di Portfolio Tracker. Non è stato importato nulla.",
      ),
    ).toBeVisible();
    await expect(page.getByText("Colonna: «# Directa synthetic fixture».")).toBeVisible();
    const columns = page.getByRole("region", { name: "Colonne attese" });
    await expect(columns).toContainText("Directa (Excel): Data operazione,");
    await expect(columns).toContainText("Riferimento ordine.");
    await expect(columns).toContainText("account, trade_date, type");
    await page.getByText("Quali formati sono supportati?").click();
    await expect(page.getByText(/^Formati supportati:/)).toBeVisible();
    await page.getByRole("button", { name: "Scegli un altro file" }).click();
    await expect(page.getByLabel("File da importare")).toBeAttached();

    // 23
    const directa = `${FIXTURES}/directa_synthetic_2024.xlsx`;
    await page.getByLabel("File da importare").setInputFiles(directa);
    await page.getByRole("button", { name: "Continua all'anteprima" }).click();
    await page.getByLabel("Importa nel conto").selectOption({ label: "Conto Directa" });
    await page.getByRole("button", { name: "Continua", exact: true }).click();
    await expect(page.getByText("Formato: Directa (Excel) · 22 righe lette")).toBeVisible();
    await expect(card("Movimenti da importare")).toHaveText("11");
    await expect(card("Già presenti, saltati")).toHaveText("0");
    await expect(card("Non importati")).toHaveText("1");
    await expect(card("Avvisi da leggere")).toHaveText("—");
    await expect(page.locator(".import-count", { hasText: "Avvisi da leggere" })).toContainText(
      "Gli avvisi vengono controllati quando non resta nulla da risolvere.",
    );
    await expect(page.getByRole("tab")).toHaveText([
      "Da importare (11)",
      "Da rivedere (1)",
      "Non riconosciuti (1)",
    ]);
    const blocking = page.locator(".import-blocking");
    await expect(blocking.getByRole("listitem")).toHaveCount(2);
    const asset = (name: string) => page.locator(".import-asset", { hasText: name });
    await asset("TEST ETF WORLD UCITS").getByLabel("Tipo").selectOption({ label: "ETF" });
    await asset("TEST AZIONE SPA").getByLabel("Tipo").selectOption({ label: "Azione" });
    await expect(blocking).toHaveCount(0);
    await expect(card("Avvisi da leggere")).toHaveText("1");
    await expect(confirmImport).toBeEnabled();

    // 24
    await confirmImport.click();
    await expect(
      page.getByRole("heading", { name: "Fatto: 11 movimenti aggiunti a «Conto Directa»" }),
    ).toBeVisible();
    await expect(card("Aggiunti")).toHaveText("11");
    await expect(card("Non importati")).toHaveText("1");
    await expect(card("Avvisi da controllare")).toHaveText("1");
    await expect(
      page.getByText("1 riga da rivedere non è stata selezionata e non è stata importata."),
    ).toBeVisible();
    await expect(
      page.getByText(/^3 titoli nuovi: i prezzi automatici sono disattivati su questa installazione/),
    ).toBeVisible();
    await expect(await accountCard(page, "Conto Directa")).toContainText("5.872,95 €");

    // 25: the same file again.
    await menu(page, "Importa");
    await page.getByLabel("File da importare").setInputFiles(directa);
    await page.getByRole("button", { name: "Continua all'anteprima" }).click();
    await expect(page.getByLabel("Importa nel conto")).toHaveValue(/.+/);
    await expect(card("Movimenti da importare")).toHaveText("0");
    await expect(card("Già presenti, saltati")).toHaveText("11");
    await expect(page.getByRole("tab", { name: "Da rivedere (1)" })).toBeVisible();
    await expect(confirmImport).toBeDisabled();
    await page.getByRole("button", { name: "Annulla", exact: true }).click();
  });

  await test.step("E. holdings: list, detail, adding (26-36)", async () => {
    // 26
    await view(page, "Conto titoli");
    await menu(page, "Titoli");
    await expect(
      page.getByText("Prezzi automatici disattivati su questa installazione: i prezzi si inseriscono a mano."),
    ).toHaveCount(1);
    const mondo = page.getByRole("row", { name: /Esempio Mondo ETF/ });
    for (const text of ["40", "100,05 €", "Solo prezzi a mano", "Stimato al costo", "4.002,00 €"]) {
      await expect(mondo).toContainText(text);
    }
    await expect(mondo.getByText("+97,50 €")).toHaveCount(2); // gross and net

    // 27
    await page.getByLabel("Tipo", { exact: true }).selectOption({ label: "Obbligazione" });
    await expect(page.getByRole("heading", { name: "Nessun risultato" })).toBeVisible();
    await expect(page.getByText("Nessun titolo corrisponde ai filtri scelti.")).toBeVisible();
    await page.getByRole("button", { name: "Azzera i filtri" }).click();

    // 28
    await page.getByRole("link", { name: "Esempio Mondo ETF" }).click();
    await expect(page.getByText("Senza ISIN: solo prezzi a mano.")).toBeVisible();
    const txs = page.getByRole("region", { name: "Transazioni di questo titolo" });
    await expect(txs.getByRole("row")).toHaveCount(3); // the header and the two transactions
    await expect(page.getByText(/^Prezzo da:/)).toHaveCount(0);
    await page.getByRole("button", { name: "Elimina tutte le transazioni del titolo…" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Annulla" }).click();
    await page.getByRole("link", { name: "Aggiungi transazione" }).click();
    await expect(page.getByLabel("Titolo", { exact: true })).not.toHaveValue("");
    await cancel.click();

    // 29
    await view(page, "Tutti i conti");
    await menu(page, "Titoli");
    await expect(page.getByRole("row", { name: /TEST ETF WORLD UCITS/ })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "PMC (EUR)" })).toBeVisible();

    // 30
    await menu(page, "Conti");
    await page.getByRole("button", { name: "Nuovo conto" }).click();
    const newAccount = page.getByRole("dialog", { name: "Nuovo conto" });
    await newAccount.getByLabel("Nome").fill("Conto test");
    await newAccount.getByRole("button", { name: "Crea conto" }).click();
    await expect(status(page, "Conto «Conto test» creato.")).toBeVisible();
    await page.getByRole("link", { name: "Aggiungi titoli che già possiedo" }).click();
    await expect(page.getByLabel("Conto", { exact: true })).toContainText("Conto test");
    await expect(page.getByLabel("Conto", { exact: true }).locator("option:checked")).toHaveText(
      "Conto test (EUR)",
    );
    await page.getByRole("button", { name: "Annulla" }).or(cancel).first().click();

    // 31
    await view(page, "Conto test");
    await menu(page, "Titoli");
    await expect(page.getByRole("heading", { name: "Nessun titolo in questa vista" })).toBeVisible();
    await expect(
      page.getByText("I titoli compaiono qui dopo il primo acquisto o la prima importazione."),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Aggiungi titoli che già possiedo" })).toHaveCount(2);
    await expect(page.getByRole("link", { name: "Aggiungi titolo" })).toHaveCount(2);

    // 32
    await page.getByRole("link", { name: "Aggiungi titolo" }).first().click();
    await expect(
      page.getByText(
        "I prezzi automatici sono disattivati su questa installazione: la ricerca non è disponibile. Puoi comunque creare il titolo a mano.",
      ),
    ).toBeVisible();
    await expect(page.getByLabel("Cerca per ISIN, nome o simbolo")).toBeDisabled();
    await expect(page.getByRole("button", { name: "Cerca" })).toBeDisabled();

    // 33
    await page.getByLabel("Nome", { exact: true }).fill("Esempio ETF Test");
    await page.getByLabel("Tipo", { exact: true }).selectOption({ label: "ETF" });
    await page.getByLabel("ISIN (facoltativo)").fill("ZZ0000087039");
    await page.getByRole("button", { name: "Crea titolo" }).click();
    await expect(
      page.getByText("«Esempio ETF Test» aggiunto. I prezzi automatici sono disattivati su questa installazione."),
    ).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toBeFocused();
    await page.getByLabel("Nome", { exact: true }).fill("Esempio BTP Test");
    await page.getByLabel("Tipo", { exact: true }).selectOption({ label: "Obbligazione" });
    await expect(page.getByLabel("Il prezzo è espresso in")).toContainText("% del nominale");
    await page.getByRole("button", { name: "Crea titolo" }).click();
    await expect(
      page.getByText("«Esempio BTP Test» aggiunto. Senza ISIN: i prezzi li inserisci tu."),
    ).toBeVisible();

    // 34
    await page.getByRole("link", { name: "Aggiungi titoli che già possiedo" }).click();
    await expect(page.getByLabel("Titolo, riga 1").locator("option:checked")).toHaveText(/Esempio BTP Test/);
    await page.getByLabel("Posseduti alla data", { exact: true }).fill("31.12.2025");
    await page.getByLabel("Nominale, riga 1").fill("3000");
    await page.getByLabel("Prezzo medio di carico, riga 1").fill("99,5");
    const rows = page.locator("table.add-rows tbody tr");
    await expect(rows.nth(0)).toContainText("2.985,00 €");
    await page.getByLabel("Titolo, riga 2").selectOption({ label: "Esempio ETF Test · EUR" });
    await page.getByLabel("Quantità, riga 2").fill("15");
    await page.getByLabel("Prezzo medio di carico, riga 2").fill("100");
    await expect(rows.nth(1)).toContainText("1.500,00 €");
    await expect(
      page.getByText("2 titoli pronti · Ogni riga diventa un acquisto che non muove la cassa."),
    ).toBeVisible();
    await page.getByRole("button", { name: "Salva 2 titoli" }).click();

    // 35
    await expect(status(page, "2 titoli aggiunti a «Conto test».")).toBeVisible();
    const btp = page.getByRole("row", { name: /Esempio BTP Test/ });
    for (const text of ["3.000", "99,50%", "Solo prezzi a mano", "Stimato al costo", "2.985,00 €"]) {
      await expect(btp).toContainText(text);
    }
    const etfTest = page.getByRole("row", { name: /Esempio ETF Test/ });
    for (const text of ["15", "100,00 €", "Stimato al costo", "1.500,00 €"]) {
      await expect(etfTest).toContainText(text);
    }
    let test_ = await accountCard(page, "Conto test");
    await expect(test_).toContainText(/Cassa\s*0,00\s€/);
    await expect(test_).toContainText("4.485,00 €");

    // 36: the page's own errors; nothing saved. The path's "a row in USD" needs a security in
    // USD, which the path has not created by then: step 39 checks the total cost in EUR of a
    // security in GBP instead.
    await menu(page, "Titoli");
    await page.getByRole("link", { name: "Aggiungi titoli che già possiedo" }).first().click();
    await page.getByLabel("Posseduti alla data", { exact: true }).fill("31/12/2025");
    await page.getByLabel("Titolo, riga 1").selectOption({ label: "Esempio ETF Test · EUR" });
    await page.getByRole("button", { name: /^Salva/ }).click();
    await expect(page.getByText("Completa la riga o svuotala.").first()).toBeVisible();
    await page.getByLabel("Titolo, riga 2").selectOption({ label: "Esempio ETF Test · EUR" });
    await expect(page.getByText("Lo stesso titolo è anche nella riga 1.").first()).toBeVisible();
    await page.getByRole("button", { name: "Annulla" }).or(cancel).first().click();
    test_ = await accountCard(page, "Conto test");
    await expect(test_).toContainText("4.485,00 €");
  });

  const holdingRow = (name: string) => page.getByRole("row", { name: new RegExp(name) });
  const positionValue = page.getByRole("region", { name: "Valore della posizione" });
  const holdingGain = page.getByRole("region", { name: "Guadagno netto su questo titolo" });

  /** Opens a holding's detail from the holdings list, in the account in view. */
  async function openHolding(name: string) {
    await menu(page, "Titoli");
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  }

  /** "Add holdings I already own" with one row, from the holdings page. */
  async function addHeld(account: string, asset: string, quantity: string, pmc: string) {
    await menu(page, "Titoli");
    await page.getByRole("link", { name: "Aggiungi titoli che già possiedo" }).first().click();
    await page.getByLabel("Conto", { exact: true }).selectOption({ label: account });
    await page.getByLabel("Posseduti alla data", { exact: true }).fill("31/12/2025");
    await page.getByLabel("Titolo, riga 1").selectOption({ label: asset });
    await page.getByLabel("Quantità, riga 1").fill(quantity);
    await page.getByLabel("Prezzo medio di carico, riga 1").fill(pmc);
  }

  await test.step("F. manual prices, editing and deleting a security (37-48)", async () => {
    // 37: "Conto sterline" in GBP and "Esempio UK Test" in GBP. First with the whole currency,
    // the case the path warns about at step 38, then corrected to pence as the path says.
    await menu(page, "Conti");
    await page.getByRole("button", { name: "Nuovo conto" }).click();
    const newAccount = page.getByRole("dialog", { name: "Nuovo conto" });
    await newAccount.getByLabel("Nome").fill("Conto sterline");
    await newAccount.getByLabel("Valuta").selectOption("GBP");
    await newAccount.getByRole("button", { name: "Crea conto" }).click();
    await expect(page.getByRole("region", { name: "Conto sterline" })).toBeVisible();
    await view(page, "Conto sterline");
    await menu(page, "Titoli");
    await page.getByRole("link", { name: "Aggiungi titolo" }).first().click();
    await page.getByLabel("Nome", { exact: true }).fill("Esempio UK Test");
    await page.getByLabel("Tipo", { exact: true }).selectOption({ label: "ETF" });
    await page.getByLabel("Valuta", { exact: true }).selectOption("GBP");
    const unit = page.getByLabel("Il prezzo è espresso in");
    await expect(unit.locator("option")).toHaveText([
      "Valuta intera (GBP)",
      "Centesimi di GBP (es. pence per GBP)",
    ]);
    await unit.selectOption({ label: "Valuta intera (GBP)" });
    await page.getByRole("button", { name: "Crea titolo" }).click();
    await expect(
      page.getByText("«Esempio UK Test» aggiunto. Senza ISIN: i prezzi li inserisci tu."),
    ).toBeVisible();

    // 38: in the whole currency 8000 is 8000 £ each: 32.000,00 £, not saved.
    await page.getByRole("link", { name: "Aggiungi titoli che già possiedo" }).click();
    const account = page.getByLabel("Conto", { exact: true });
    await expect(account.locator("option:checked")).toHaveText("Conto sterline (GBP)");
    await page.getByLabel("Posseduti alla data", { exact: true }).fill("31/12/2025");
    await page.getByLabel("Quantità, riga 1").fill("4");
    await page.getByLabel("Prezzo medio di carico, riga 1").fill("8000");
    const row1 = page.locator("table.add-rows tbody tr").first();
    await expect(row1).toContainText("32.000,00 £");
    await expect(row1).toContainText("in GBP");
    await page.getByRole("button", { name: "Annulla" }).or(cancel).first().click();
    // The path's way back: Titoli → "1 titolo senza transazioni · Mostra" → Apri → Modifica.
    await menu(page, "Titoli");
    await expect(page.getByText("1 titolo senza transazioni")).toBeVisible();
    await page.getByRole("button", { name: "Mostra" }).click();
    await page.getByRole("link", { name: "Apri" }).click();
    await page.getByRole("button", { name: "Modifica titolo" }).click();
    let edit = page.getByRole("dialog");
    await edit.getByLabel("Il prezzo è espresso in").selectOption({
      label: "Centesimi di GBP (es. pence per GBP)",
    });
    await edit.getByRole("button", { name: "Salva" }).click();
    await expect(status(page, "Titolo aggiornato.")).toBeVisible();
    // With pence: 8000 pence = 80 £ each, 320,00 £, no total cost (same currency).
    await addHeld("Conto sterline (GBP)", "Esempio UK Test · GBP", "4", "8000");
    await expect(row1).toContainText("in centesimi di GBP");
    await expect(row1).toContainText("320,00 £");
    await expect(page.getByLabel("Costo totale, riga 1")).toHaveCount(0);
    await page.getByRole("button", { name: "Salva 1 titolo" }).click();
    await expect(status(page, "1 titolo aggiunto a «Conto sterline».")).toBeVisible();
    for (const text of ["4", "80,00 £", "Stimato al costo", "320,00 £"]) {
      await expect(holdingRow("Esempio UK Test")).toContainText(text);
    }

    // 39: the same security in the euro account: the total cost in EUR is the cost.
    await addHeld("Conto test (EUR)", "Esempio UK Test · GBP", "10", "8000");
    await expect(
      page.getByText("Quotato in GBP: indica il costo totale in EUR come addebitato dal broker."),
    ).toBeVisible();
    await expect(row1).toContainText("in EUR");
    await page.getByLabel("Costo totale, riga 1").fill("920");
    await page.getByRole("button", { name: "Salva 1 titolo" }).click();
    await expect(status(page, "1 titolo aggiunto a «Conto test».")).toBeVisible();
    await view(page, "Conto test");
    await menu(page, "Titoli");
    for (const text of ["10", "92,00 €", "Stimato al costo", "920,00 €"]) {
      await expect(holdingRow("Esempio UK Test")).toContainText(text);
    }
    const test_ = await accountCard(page, "Conto test");
    await expect(test_).toContainText("5.405,00 €");
    await expect(test_).toContainText(/Cassa\s*0,00\s€/);
    const pounds = page.getByRole("region", { name: "Conto sterline" });
    await expect(pounds).toContainText("320,00 £");
    await expect(pounds).toContainText("Controvalore in EUR non disponibile: manca il tasso di cambio.");

    // 40
    await openHolding("Esempio BTP Test");
    await expect(page.getByText("Senza ISIN: solo prezzi a mano.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Inserisci un prezzo a mano" })).toBeVisible();
    await page.getByRole("link", { name: "Prezzi a mano", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Prezzi a mano", level: 1 })).toBeVisible();
    await expect(page.getByText("Esempio BTP Test · Obbligazione · EUR")).toBeVisible();
    const day = page.getByLabel("Data", { exact: true });
    await expect(day).toHaveValue(typed(TODAY));
    await expect(page.getByLabel("Prezzo (% del nominale)")).toBeVisible();
    await expect(page.getByRole("heading", { name: "I tuoi prezzi a mano" })).toBeVisible();
    await expect(page.getByText("I prezzi automatici non sono elencati qui.")).toBeVisible();
    await expect(page.getByText("Nessun prezzo a mano per questo titolo.")).toBeVisible();

    // 41
    await day.fill(typed(TOMORROW));
    await page.getByLabel("Prezzo (% del nominale)").fill("100");
    await page.getByRole("button", { name: "Salva prezzo" }).click();
    await expect(page.getByText("La data non può essere nel futuro.")).toBeVisible();
    await expect(page.getByText("Nessun prezzo a mano per questo titolo.")).toBeVisible();
    await day.fill(typed(TODAY));
    await page.getByLabel("Prezzo (% del nominale)").fill("101");
    await page.getByRole("button", { name: "Salva prezzo" }).click();
    await expect(status(page, `Prezzo del ${shown(TODAY)} salvato.`)).toBeVisible();
    await expect(page.getByText(`Prezzo in uso: 101,00% del ${shown(TODAY)} (a mano)`)).toBeVisible();

    // 42
    await page.getByRole("link", { name: "← Esempio BTP Test" }).click();
    await expect(positionValue).toContainText("3.030,00 €");
    await expect(positionValue).toContainText(`Nominale 3.000 × 101,00% · prezzo del ${shown(TODAY)}`);
    await expect(holdingGain).toContainText("+45,00 €");

    // 43
    await page.getByRole("link", { name: "Prezzi a mano", exact: true }).click();
    await page.getByRole("button", { name: `Modifica il prezzo del ${shown(TODAY)}` }).click();
    await expect(page.getByLabel("Data", { exact: true })).not.toBeEditable(); // read-only
    await page.getByLabel("Prezzo (% del nominale)").fill("99");
    await page.getByRole("button", { name: "Salva prezzo" }).click();
    await expect(page.getByText(`Prezzo in uso: 99,00% del ${shown(TODAY)} (a mano)`)).toBeVisible();
    await page.getByRole("link", { name: "← Esempio BTP Test" }).click();
    await expect(positionValue).toContainText("2.970,00 €");
    await expect(holdingGain).toContainText(/.15,00\s€/);

    // 44
    await page.getByRole("link", { name: "Prezzi a mano", exact: true }).click();
    const remove = page.getByRole("button", { name: `Elimina il prezzo del ${shown(TODAY)}` });
    await remove.click();
    await page.getByRole("dialog").getByRole("button", { name: "Annulla" }).click();
    await expect(page.getByText(`Prezzo in uso: 99,00% del ${shown(TODAY)} (a mano)`)).toBeVisible();
    await remove.click();
    await page.getByRole("dialog").getByRole("button", { name: "Elimina prezzo" }).click();
    await expect(status(page, `Prezzo del ${shown(TODAY)} eliminato.`)).toBeVisible();
    await expect(page.getByText("Nessun prezzo a mano per questo titolo.")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Prezzi a mano", level: 1 })).toBeFocused();
    await page.getByRole("link", { name: "← Esempio BTP Test" }).click();
    await expect(page.getByText("Stimato al costo", { exact: true })).toBeVisible();
    await expect(positionValue).toContainText("2.985,00 €");

    // 45
    await openHolding("Esempio ETF Test");
    await expect(
      page.getByText("Prezzi automatici disattivati su questa installazione: inseriscili a mano."),
    ).toBeVisible();
    await page.getByRole("link", { name: "Inserisci un prezzo a mano" }).click();
    await page.getByLabel("Prezzo (EUR)").fill("104");
    await page.getByRole("button", { name: "Salva prezzo" }).click();
    await expect(status(page, `Prezzo del ${shown(TODAY)} salvato.`)).toBeVisible();
    await page.getByRole("link", { name: "← Esempio ETF Test" }).click();
    await expect(page.getByText("Prezzo a mano", { exact: true })).toBeVisible();
    await expect(positionValue).toContainText("1.560,00 €");
    await expect(holdingGain).toContainText("+60,00 €");
    await expect(await accountCard(page, "Conto test")).toContainText("5.465,00 €");

    // 46
    await view(page, "Tutti i conti");
    await menu(page, "Titoli");
    await expect(page.getByText(/senza transazioni/)).toHaveCount(0);
    await page.getByRole("link", { name: "Esempio ETF Test", exact: true }).click();
    await page.getByRole("button", { name: "Modifica titolo" }).click();
    edit = page.getByRole("dialog");
    for (const label of ["Tipo", "Valuta", "ISIN (facoltativo)", "Il prezzo è espresso in"]) {
      await expect(edit.getByLabel(label, { exact: true })).toBeDisabled();
    }
    await expect(edit).toContainText(
      "Tipo, valuta, unità di prezzo e ISIN si possono cambiare solo finché nessuna transazione usa il titolo.",
    );
    await expect(edit).toContainText("Si può eliminare solo un titolo senza transazioni.");
    await edit.getByRole("button", { name: "Annulla" }).click();

    // 47
    await menu(page, "Titoli");
    await page.getByRole("link", { name: "Aggiungi titolo" }).first().click();
    await page.getByLabel("Nome", { exact: true }).fill("Esempio Temporaneo");
    await page.getByLabel("Tipo", { exact: true }).selectOption({ label: "ETF" });
    await page.getByRole("button", { name: "Crea titolo" }).click();
    await expect(page.getByText(/^«Esempio Temporaneo» aggiunto\./)).toBeVisible();
    await page.getByRole("link", { name: "Aggiungi titoli che già possiedo" }).click();
    await page.getByLabel("Conto", { exact: true }).selectOption({ label: "Conto test (EUR)" });
    await page.getByLabel("Posseduti alla data", { exact: true }).fill("31/12/2025");
    await page.getByLabel("Quantità, riga 1").fill("2");
    await page.getByLabel("Prezzo medio di carico, riga 1").fill("50");
    await page.getByRole("button", { name: "Salva 1 titolo" }).click();
    await expect(status(page, "1 titolo aggiunto a «Conto test».")).toBeVisible();
    await expect(await accountCard(page, "Conto test")).toContainText("5.565,00 €");
    await view(page, "Conto test");
    await openHolding("Esempio Temporaneo");
    await page.getByRole("button", { name: "Modifica titolo" }).click();
    edit = page.getByRole("dialog");
    await edit.getByLabel("Nome", { exact: true }).fill("Esempio Temporaneo Bis");
    await edit.getByLabel("Simbolo (facoltativo)").fill("ZTMP");
    await edit.getByRole("button", { name: "Salva" }).click();
    await expect(status(page, "Titolo aggiornato.")).toBeVisible();
    await page.getByRole("button", { name: "Elimina tutte le transazioni del titolo…" }).click();
    const all = page.getByRole("dialog");
    await all.getByLabel("Scrivi il nome del titolo per confermare").fill("Esempio Temporaneo Bis");
    await all.getByRole("button", { name: "Elimina 1 transazione" }).click();
    await expect(status(page, "1 transazione eliminata.")).toBeVisible();
    await expect(page.getByText("1 titolo senza transazioni")).toBeVisible();
    await expect(await accountCard(page, "Conto test")).toContainText("5.465,00 €");

    // 48
    await menu(page, "Titoli");
    await page.getByRole("button", { name: "Mostra" }).click();
    await expect(page.getByRole("list", { name: "1 titolo senza transazioni" })).toBeFocused();
    await page.getByRole("link", { name: "Apri" }).click();
    await expect(page.getByText("Questo titolo non ha transazioni.")).toBeVisible();
    await page.getByRole("link", { name: "Prezzi a mano", exact: true }).click();
    await page.getByLabel("Prezzo (EUR)").fill("55");
    await page.getByRole("button", { name: "Salva prezzo" }).click();
    await expect(status(page, `Prezzo del ${shown(TODAY)} salvato.`)).toBeVisible();
    await page.getByRole("link", { name: "← Esempio Temporaneo Bis" }).click();
    await page.getByRole("button", { name: "Modifica titolo" }).click();
    edit = page.getByRole("dialog");
    for (const label of ["Tipo", "Valuta", "ISIN (facoltativo)", "Il prezzo è espresso in"]) {
      await expect(edit.getByLabel(label, { exact: true })).toBeEnabled();
    }
    await edit.getByLabel("Valuta", { exact: true }).selectOption("USD");
    await edit.getByRole("button", { name: "Salva" }).click();
    await expect(
      edit.getByText("Hai prezzi a mano per questo titolo: eliminali prima di cambiare valuta o unità."),
    ).toBeVisible();
    await edit.getByRole("button", { name: "Annulla" }).click();
    await page.getByRole("button", { name: "Modifica titolo" }).click();
    edit = page.getByRole("dialog");
    await edit.getByRole("button", { name: "Elimina titolo" }).click();
    await page.getByRole("dialog", { name: "Eliminare «Esempio Temporaneo Bis»?" })
      .getByRole("button", { name: "Elimina titolo" })
      .click();
    await expect(status(page, "«Esempio Temporaneo Bis» eliminato.")).toBeVisible();
    await expect(await accountCard(page, "Conto test")).toContainText("5.465,00 €");
  });

  const region = (name: string) => page.getByRole("region", { name, exact: true });
  const table = (name: string) => page.getByRole("table", { name, exact: true });
  const tableRows = (name: string) => table(name).locator("tbody tr");

  await test.step("G. dashboard and charts (49-59)", async () => {
    // 49
    await view(page, "Tutti i conti");
    await menu(page, "Dashboard");
    await expect(
      page.getByRole("heading", { name: "Ciao collaudo, ecco i tuoi investimenti", level: 1 }),
    ).toBeVisible();
    await expect(
      page.getByText(`Vista: tutti i conti (5) · valori in EUR al ${shown(TODAY)}`),
    ).toBeVisible();
    for (const text of [
      "Gli importi in altre valute sono convertiti al cambio più recente, anche guadagni e proventi passati.",
      "Prezzi automatici disattivati su questa installazione: i prezzi si inseriscono a mano.",
      "Parziale: manca un tasso di cambio, quindi una parte del valore non è inclusa.",
      "Esclusi i conti in GBP: manca il tasso di cambio verso EUR.",
    ]) {
      await expect(page.getByText(text).first()).toBeVisible();
    }

    // 50
    await expect(region("Valore totale")).toContainText("23.614,35 €");
    await expect(region("Valore totale")).toContainText("Titoli 11.645,90 € · Cassa 11.968,45 €");
    await expect(region("Valore totale")).toContainText("Parziale");
    await expect(region("Capitale investito")).toContainText("11.585,90 €");
    await expect(region("Guadagno netto")).toContainText("+216,05 €");
    await expect(region("Guadagno netto")).toContainText("Lordo +227,50 € · imposte 11,45 €");

    // 51
    const gains = region("Da dove viene il guadagno").getByRole("table");
    const gainRow = (name: string) => gains.getByRole("row", { name: new RegExp(`^${name}`) });
    await expect(gainRow("Non realizzato")).toContainText(/\+60,00\s€.*\+60,00\s€/);
    await expect(gainRow("Realizzato")).toContainText(/\+97,50\s€.*\+97,50\s€/);
    await expect(gainRow("Dividendi e cedole")).toContainText(/\+70,00\s€.*\+58,55\s€/);
    await expect(gainRow("Guadagno totale")).toContainText(/\+227,50\s€.*\+216,05\s€/);
    await expect(
      page.getByText("Imposte trattenute: 11,45 € (0,00 € sulle vendite, 11,45 € sui proventi)."),
    ).toBeVisible();

    // 52
    await expect(tableRows("Per tipo")).toHaveText([
      /Cassa.*11\.968,45\s€.*50,68%/,
      /ETF.*8\.407,40\s€.*35,60%/,
      /Obbligazione.*2\.985,00\s€.*12,64%/,
      /Azione.*253,50\s€.*1,07%/,
    ]);
    await expect(tableRows("Per valuta")).toHaveText([
      /EUR.*22\.694,35\s€.*96,10%/,
      /GBP.*920,00\s€.*3,90%/,
    ]);
    await expect(tableRows("Per titolo")).toHaveText([
      /Esempio Mondo ETF.*4\.002,00\s€/,
      /Esempio BTP Test.*2\.985,00\s€/,
      /TEST ETF WORLD UCITS.*1\.925,40\s€/,
      /Esempio ETF Test.*1\.560,00\s€/,
      /Esempio UK Test.*920,00\s€/,
      /TEST AZIONE SPA.*253,50\s€/,
    ]);

    // 53
    await expect(page.getByText("Servono almeno due titoli con un prezzo per confrontarli.")).toBeVisible();
    await expect(page.getByText("Esclusi 5 titoli stimati al costo.")).toBeVisible();

    // 54: 12 months at 0,00 € (and so no chart of them).
    await expect(page.getByText("Dall'inizio: 70,00 € lordi · 58,55 € netti")).toBeVisible();
    // One row per year from the first one with income to this year.
    const thisYear = TODAY.slice(0, 4);
    await expect(tableRows("Per anno")).toHaveText([
      /2024.*70,00\s€.*58,55\s€/,
      ...Array.from({ length: Number(thisYear) - 2024 }, (_, i) => new RegExp(`${2025 + i}0,00\\s€0,00\\s€`)),
    ]);

    // 55 and 56: the charts' look, their colours and patterns in each theme, and the pointer
    // over them need a human. Each theme switches, and axe passes on the dashboard in each.
    for (const [label, theme] of [
      ["Sobrio", "sobrio"],
      ["Moderno", "moderno"],
      ["Ambra", "ambra"],
      ["Grafite", "grafite"],
    ] as const) {
      await menu(page, "Impostazioni");
      await page.locator("label", { hasText: label }).click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await menu(page, "Dashboard");
      await expect(region("Valore totale")).toContainText("23.614,35 €");
      await expectAccessible(page, `dashboard, ${theme}`);
    }

    // 57
    await view(page, "Conto test");
    await expect(region("Valore totale")).toContainText("5.465,00 €");
    await expect(region("Capitale investito")).toContainText("5.405,00 €");
    await expect(region("Guadagno netto")).toContainText("+60,00 €");
    await expect(page.getByText("Parziale", { exact: true })).toHaveCount(0);

    // 58: the empty account (screen 19), then the account in pounds.
    await view(page, "Conto estero");
    await expect(page.getByRole("heading", { name: "Questo conto non ha ancora titoli" })).toBeVisible();
    await expect(page.getByText("Scegli come iniziare:")).toBeVisible();
    for (const name of [
      "Aggiungi titoli che già possiedo",
      "Importa un file",
      "Aggiungi una transazione",
    ]) {
      await expect(main_.getByRole("link", { name })).toBeVisible();
    }
    await main_.getByRole("link", { name: "Aggiungi una transazione" }).click();
    await expect(page.getByLabel("Conto in vista").locator("option:checked")).toHaveText("Conto estero");
    await view(page, "Conto sterline");
    await menu(page, "Dashboard");
    await expect(region("Valore totale")).toContainText("320,00 £");

    // 59: a dividend in Conto titoli.
    await view(page, "Conto titoli");
    await menu(page, "Transazioni");
    await page.getByRole("link", { name: "Aggiungi transazione" }).click();
    await page.getByRole("radio", { name: "Dividendo" }).check();
    await page.getByLabel("Titolo", { exact: true }).selectOption({ label: "Esempio Mondo ETF · EUR" });
    await page.getByLabel("Importo lordo (EUR)").fill("25");
    await page.getByLabel("Imposte trattenute (EUR)").fill("6,50");
    await page.getByRole("button", { name: "Salva", exact: true }).click();
    await expect(status(page, "Transazione salvata.")).toBeVisible();
    await menu(page, "Dashboard");
    // The bar of the current month is drawn in the chart: its look needs a human.
    await expect(page.getByText("Dall'inizio: 25,00 € lordi · 18,50 € netti")).toBeVisible();
    await expect(region("Valore totale")).toContainText("Cassa 6.114,00 €");
    await expect(region("Guadagno netto")).toContainText("+116,00 €");
    await view(page, "Tutti i conti");
    await expect(page.getByText("Dall'inizio: 95,00 € lordi · 77,05 € netti")).toBeVisible();
    await expect(region("Guadagno netto")).toContainText("+234,55 €");
    await expect(region("Guadagno netto")).toContainText("Parziale");
  });

  await test.step("H. errors and page not found (60-62)", async () => {
    // 60
    await page.goto("/pagina-inesistente");
    await expect(page.getByRole("heading", { name: "Pagina non trovata" })).toBeVisible();
    await expect(page.getByText("L'indirizzo non esiste o il titolo è stato eliminato.")).toBeVisible();
    await page.getByRole("link", { name: "Torna alla dashboard" }).click();
    await expect(
      page.getByRole("heading", { name: "Ciao collaudo, ecco i tuoi investimenti", level: 1 }),
    ).toBeVisible();

    // 61: the server stops while the transactions are open.
    await menu(page, "Transazioni");
    await expect(page.getByRole("table")).toBeVisible();
    await stop();
    await page.getByLabel("Tipo", { exact: true }).selectOption({ label: "Acquisto" });
    await expect(page.getByRole("heading", { name: "Qualcosa non ha funzionato" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      page.getByText(
        "Il server non è raggiungibile. Controlla la connessione e riprova. Se succede ancora, controlla i log del contenitore. Codice: NETWORK.",
      ),
    ).toBeVisible();

    // 62
    await start(page);
    await page.getByRole("button", { name: "Riprova" }).click();
    await expect(page.getByRole("row", { name: /Acquisto/ }).first()).toBeVisible();
  });

  await test.step("I. persistence (63-64)", async () => {
    // 63: a restart. 64 (down and up) is the same for this server: one process, one data folder;
    // the container's own volume is the maintainer's check before M7.
    await stop();
    await start(page);
    await page.goto("/accounts");
    await expect(page.getByRole("heading", { name: "Conti", level: 1 })).toBeVisible();
    await expect(page.getByRole("region", { name: "Conto titoli" })).toContainText(/Cassa\s*6\.114,00\s€/);
    await expect(page.getByRole("region", { name: "Conto test" })).toContainText("5.465,00 €");
    await expect(page.getByRole("region", { name: "Conto sterline" })).toContainText("320,00 £");
    await view(page, "Tutti i conti");
    await menu(page, "Transazioni");
    await expect(page.getByText("Vista: tutti i conti (5) · 19 movimenti")).toBeVisible();
  });
});
