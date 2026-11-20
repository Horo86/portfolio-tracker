// Screenshots for the maintainer's review of the phone and tablet layout (D-189); opt-in and
// local, never in CI, never committed. `npm run screenshots:review` from the frontend folder
// (it builds first); it needs the backend's environment and Playwright's Chromium, as
// `npm run screenshots`.
//
// It seeds the invented demo data in a throwaway folder, starts the app on :8716 with market
// data off, and takes each page as the demo investor and Administration as the demo
// administrator, in Italian, at 390x844 (phone) and 820x1180 (tablet), with the light Moderno
// theme and with dark, the "Altro" menu open too; Administration also at its Security and price
// sources cards, with the new user dialog and the Security card's confirmation open (nothing
// is saved), and on phones the Security card's errors; plus the dashboard in dark at 844x390
// (a phone on its side). The dashboard, the holdings and three holdings' pages (a share with
// several transactions, the bond with its manual price, and a long-named security the script
// adds to this throwaway copy) are also taken top to bottom (D-191). The images go to
// frontend/.review-screenshots/ (ignored by git); the data folder is deleted at the end.
// The transactions (D-192): the list over all accounts and over one, a deposit, the selection
// bar with one, two and several ticked (scrolled so that it is stuck), the bulk dialogs, the
// form with a purchase filled in, the edit page and its delete dialog; nothing is saved. On the
// throwaway copy the long-named security also gets a recent dividend that does not move cash.
// `--only=transactions` takes only these, for a quicker look.
// The import (D-193): step 1 empty and with a long file name, the account step, the preview of
// the Directa fixture (top with the blocking card, ready, the "to review" and "not recognized"
// tabs with their notes, scrolled so that the confirmation bar is stuck, top to bottom), the
// sales fixture with its linked sale and, for the screenshot only, an invented tax to add by
// hand put into the preview's answer in the browser; an invented CSV with a long file name, a
// security whose name is one long word and notes; the summary of an invented CSV of two
// accounts (confirmed on this throwaway copy). Also the bar stuck at 320 px. Nothing else is
// saved. `--only=import` takes only these.
import { mkdirSync, rmSync } from "node:fs";

import { chromium } from "@playwright/test";

import { ADMIN, CSRF, DEMO, FRONTEND, freshPrices, ready, startDemo, stop } from "./demo-server.mjs";

const DATA = `${FRONTEND}/.data-review`; // ignored by git (.data*/)
const OUT = `${FRONTEND}/.review-screenshots`; // ignored by git
const PORT = 8716;
const BASE = `http://127.0.0.1:${PORT}`;

const SIZES = {
  phone: { width: 390, height: 844 },
  narrow: { width: 320, height: 640 },
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

// The pages taken top to bottom: [name, path or the security's name on the holdings list].
const FULL = [
  ["dashboard", "/"],
  ["holdings", "/holdings"],
  ["holding-share", "Esempio Industrie SpA"],
  ["holding-bond", "Esempio Titolo di Stato 2031"],
  ["holding-long-name", null], // LONG_NAME
];
// Invented, on the throwaway copy only: a long name with spaces, as in e2e/longNames.ts.
const LONG_NAME =
  "Esempio Fondo Globale Mercati Sviluppati Azionario Indice UCITS ETF Accumulazione Coperto in Euro";

async function shoot(page, name, fullPage = false) {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    path: `${OUT}/${name}.png`,
    animations: "disabled",
    caret: "hide",
    fullPage,
  });
  console.log(`.review-screenshots/${name}.png`);
}

/** Adds the long-named security to the demo's EUR account: one purchase and a manual price. */
async function addLongName(api) {
  const accounts = await (await api.get("/api/v1/accounts")).json();
  const account = accounts.find((a) => a.currency === "EUR");
  const asset = await api.post("/api/v1/assets", {
    data: { name: LONG_NAME, asset_type: "ETF", quote_currency: "EUR", quote_unit: "CURRENCY" },
    headers: CSRF,
  });
  if (!asset.ok()) throw new Error(`long-named security refused (${asset.status()})`);
  const { id } = await asset.json();
  const bought = await api.post("/api/v1/transactions", {
    data: {
      account_id: account.id, type: "BUY", trade_date: "2025-04-07", asset_id: id,
      quantity: "30", unit_price: "40.00", gross_amount: "1200.00", fees: "1.50",
      total_amount: "1201.50",
    }, // prettier-ignore
    headers: CSRF,
  });
  if (!bought.ok()) throw new Error(`purchase refused (${bought.status()})`);
  const day = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  const priced = await api.put(`/api/v1/assets/${id}/prices`, {
    data: { day, close: "41.80" },
    headers: CSRF,
  });
  if (!priced.ok()) throw new Error(`price refused (${priced.status()})`);
  // A recent dividend that does not move cash, so it is the list's first card.
  const paid = await api.post("/api/v1/transactions", {
    data: {
      account_id: account.id, type: "DIVIDEND", trade_date: day, asset_id: id,
      gross_amount: "18.00", taxes: "4.68", total_amount: "13.32", affects_cash: false,
      notes: "Esempio di nota lunga: dividendo registrato senza muovere la cassa del conto.",
    }, // prettier-ignore
    headers: CSRF,
  });
  if (!paid.ok()) throw new Error(`dividend refused (${paid.status()})`);
}

// Invented import files (D-193). The Directa ones are the synthetic fixtures of the tests.
const DIRECTA = `${FRONTEND}/../tests/fixtures/directa/directa_synthetic_2024.xlsx`;
const SALES = `${FRONTEND}/../tests/fixtures/directa/directa_synthetic_sales_2025.xlsx`;
const LONG_FILE =
  "movimenti-conto-esempio-esportazione-completa-dal-primo-gennaio-al-trentuno-dicembre-versione-finale-rivista.csv";
const ONE_WORD = "EsempioFondoGlobaleMercatiSviluppatiAzionarioIndiceAccumulazioneCopertoInEuro";
const CSV_HEAD =
  "account,account_currency,trade_date,type,isin,ticker,asset_name,quantity,unit_price,gross_amount,fees,taxes,total_amount,external_ref";

/** An account without a currency, a long-named new security and a sale of more than is held:
 * the blocking card and notes. Never confirmed. */
function blockedCsv(tag) {
  return [
    CSV_HEAD,
    `Conto CSV,,2026-03-02,DEPOSIT,,,,,,,,,1000.00,R-${tag}-1`,
    `Conto CSV,,2026-03-03,BUY,ZZ0000002012,,${ONE_WORD},10,12.50,125.00,1.00,,126.00,R-${tag}-2`,
    `Conto CSV,,2026-03-04,BUY,ZZ0000002012,,${ONE_WORD},5,20.00,100.00,1.00,,101.00,R-${tag}-3`,
    `Conto CSV,,2026-03-05,SELL,ZZ0000002012,,${ONE_WORD},30,13.00,390.00,1.00,,389.00,R-${tag}-4`,
    `Conto CSV,,2026-03-06,PIPPO,,,,,,,,,10.00,R-${tag}-5`,
  ].join("\n");
}

/** Two accounts with deposits and a withdrawal each: the summary's table of accounts. */
function twoAccountsCsv(tag) {
  const lines = [CSV_HEAD];
  for (const [n, name] of [[1, "Conto Esempio Uno"], [2, "Conto Esempio Due"]]) {
    lines.push(`${name},EUR,2026-02-02,DEPOSIT,,,,,,,,,${n}000.00,S-${tag}-${n}-1`);
    lines.push(`${name},EUR,2026-02-09,DEPOSIT,,,,,,,,,250.00,S-${tag}-${n}-2`);
    lines.push(`${name},EUR,2026-02-16,WITHDRAWAL,,,,,,,,,100.00,S-${tag}-${n}-3`);
  }
  return lines.join("\n");
}

/** The heading `name` near the top of the window. */
async function toHeading(page, name) {
  const heading = page.getByRole("heading", { name });
  await heading.waitFor();
  await heading.evaluate((el) => window.scrollTo(0, window.scrollY + el.getBoundingClientRect().top - 24));
}

async function chooseFile(page, file) {
  await page.getByLabel("File da importare").setInputFiles(file);
  await page.getByRole("button", { name: "Continua all'anteprima" }).click();
}

async function previewReady(page) {
  await page.getByRole("tablist", { name: "Righe per categoria" }).waitFor();
  await page.locator(".confirm-bar").waitFor();
}

/** Gives each new security of the file a type, so that nothing blocks the confirmation. */
async function chooseTypes(page) {
  const types = page.locator(".import-asset select").filter({ has: page.locator('option[value="ETF"]') });
  for (const select of await types.all()) {
    if ((await select.inputValue()) === "") await select.selectOption("ETF");
  }
  await page.locator(".import-blocking").waitFor({ state: "detached" });
}

/** Scrolls the window so that the confirmation bar is stuck over the lines. */
async function stuckBar(page) {
  await page.evaluate(() => {
    const lines = document.querySelector('[role="tablist"]');
    window.scrollTo(0, window.scrollY + lines.getBoundingClientRect().top - 80);
  });
}

/** The import's screens (D-193). Only the two-account CSV is confirmed. */
async function importShots(page, tag) {
  await open(page, "/import");
  await shoot(page, `${tag}-import-file`);
  await page.getByLabel("File da importare").setInputFiles({
    name: LONG_FILE,
    mimeType: "text/csv",
    buffer: Buffer.from(blockedCsv(tag)),
  });
  await shoot(page, `${tag}-import-file-chosen`);

  // The Directa fixture: the account step, then the preview with its blocking card.
  await open(page, "/import");
  await chooseFile(page, DIRECTA);
  await page.getByLabel("Importa nel conto").waitFor();
  await shoot(page, `${tag}-import-account`);
  await page.getByLabel("Importa nel conto").selectOption({ label: "Conto titoli" });
  await page.getByRole("button", { name: "Continua", exact: true }).click();
  await previewReady(page);
  await shoot(page, `${tag}-preview-blocking`);
  await shoot(page, `${tag}-preview-blocking-full`, true);
  await chooseTypes(page);
  await page.getByRole("tab", { name: /^Da importare/ }).click();
  await shoot(page, `${tag}-preview-ready`);
  await stuckBar(page);
  await shoot(page, `${tag}-preview-stuck`);
  await page.getByRole("tab", { name: /^Da rivedere/ }).click();
  await stuckBar(page);
  await shoot(page, `${tag}-preview-tab-review`);
  await page.getByRole("tab", { name: /^Non riconosciuti/ }).click();
  await stuckBar(page);
  await shoot(page, `${tag}-preview-tab-not-recognized`);
  await page.getByRole("tab", { name: /^Da importare/ }).click();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await shoot(page, `${tag}-preview-end`);
  await shoot(page, `${tag}-preview-full`, true);

  // The sales fixture: the linked sale, and an invented tax to add by hand (this page only).
  await page.route("**/api/v1/imports/preview", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (Array.isArray(body.notices)) body.notices = [
      ...body.notices,
      { code: "SALE_TAX_TO_ADD_BY_HAND", params: { date: "2025-05-20", amount: "8.45", line: "7" } },
    ];
    await route.fulfill({ response, json: body });
  });
  await open(page, "/import");
  await chooseFile(page, SALES);
  await page.getByLabel("Importa nel conto").selectOption({ label: "Conto titoli" });
  await page.getByRole("button", { name: "Continua", exact: true }).click();
  await previewReady(page);
  await shoot(page, `${tag}-preview-sales-blocking`);
  await chooseTypes(page);
  await toHeading(page, "Imposta sulla plusvalenza da aggiungere a mano");
  await shoot(page, `${tag}-preview-sales`);
  await toHeading(page, "Vendite con imposta collegata per data");
  await shoot(page, `${tag}-preview-sales-linked`);
  await page.unroute("**/api/v1/imports/preview");

  // The long names: a file name, a security of one long word, notes.
  await open(page, "/import");
  await chooseFile(page, { name: LONG_FILE, mimeType: "text/csv", buffer: Buffer.from(blockedCsv(tag)) });
  await previewReady(page);
  await shoot(page, `${tag}-preview-long`);
  await stuckBar(page);
  await shoot(page, `${tag}-preview-long-lines`);
  await shoot(page, `${tag}-preview-long-full`, true);

  // The summary of two accounts (saved on this throwaway copy).
  await open(page, "/import");
  await chooseFile(page, { name: "due-conti.csv", mimeType: "text/csv", buffer: Buffer.from(twoAccountsCsv(tag)) });
  await previewReady(page);
  await page.getByRole("button", { name: /^Conferma l'importazione/ }).click();
  await page.getByRole("button", { name: "Importa un altro file" }).waitFor();
  await shoot(page, `${tag}-import-done`);
  await shoot(page, `${tag}-import-done-full`, true);
}

/** Scrolls the window by `y` px from the top. */
async function scrollTo(page, y) {
  await page.evaluate((top) => window.scrollTo(0, top), y);
}

/** The transactions' pages (D-192). Nothing is saved: every dialog is closed with Annulla. */
async function transactionShots(page, tag) {
  await open(page, "/transactions");
  await page.getByRole("table").waitFor();
  await shoot(page, `${tag}-tx-list`);
  const ticks = page.getByRole("checkbox", { name: /^Seleziona: / });
  const select = page.getByRole("checkbox", { name: "Seleziona tutte le righe di questa pagina" });

  // One ticked: "select all" partly ticked; the bar at the top of the list.
  await ticks.nth(0).check();
  await shoot(page, `${tag}-tx-one-selected`);
  await scrollTo(page, 900);
  await shoot(page, `${tag}-tx-one-selected-scrolled`);
  // Two, scrolled: the bar stuck at the top.
  await ticks.nth(4).check();
  await scrollTo(page, 1500);
  await shoot(page, `${tag}-tx-two-selected-scrolled`);
  // Several.
  for (const n of [5, 6, 7, 8]) await ticks.nth(n).check();
  await scrollTo(page, 2200);
  await shoot(page, `${tag}-tx-six-selected-scrolled`);
  await page.getByRole("button", { name: "Modifica selezionate" }).click();
  await page.getByRole("dialog").waitFor();
  await shoot(page, `${tag}-tx-bulk-edit`);
  await page.getByRole("button", { name: "Annulla" }).click();
  await page.getByRole("button", { name: "Elimina selezionate" }).click();
  await page.getByRole("dialog").waitFor();
  await shoot(page, `${tag}-tx-bulk-delete`);
  await page.getByRole("button", { name: "Annulla" }).click();
  await select.check();
  await scrollTo(page, 0);
  await shoot(page, `${tag}-tx-all-selected`);
  await shoot(page, `${tag}-full-tx-list`, true);

  // One account in view: no "Conto" line; then its deposits (no security).
  const accounts = await (await page.request.get("/api/v1/accounts")).json();
  const eur = accounts.find((a) => a.currency === "EUR");
  await open(page, `/transactions?account=${eur.id}`);
  await page.getByRole("table").waitFor();
  await shoot(page, `${tag}-tx-one-account`);
  await open(page, `/transactions?account=${eur.id}&type=DEPOSIT`);
  await page.getByRole("table").waitFor();
  await shoot(page, `${tag}-tx-deposits`);

  // The form with a purchase filled in, top to bottom; nothing is saved.
  await open(page, `/transactions/new?account=${eur.id}`);
  const security = page.getByLabel("Titolo", { exact: true });
  const option = await security
    .locator("option")
    .filter({ hasText: "Esempio Industrie SpA" })
    .first()
    .textContent();
  await security.selectOption({ label: option });
  await page.getByLabel("Data", { exact: true }).fill("03/11/2026");
  await page.getByLabel("Quantità").fill("25");
  await page.getByLabel(/^Prezzo unitario/).fill("13,40");
  await page.getByLabel(/^Commissioni/).fill("2,50");
  await page.getByRole("complementary").locator(".summary-rows").first().waitFor();
  await page.waitForLoadState("networkidle");
  await page.getByLabel(/^Commissioni/).blur();
  await shoot(page, `${tag}-tx-form`);
  await shoot(page, `${tag}-full-tx-form`, true);

  // The edit page of the list's first transaction, then its delete dialog.
  await open(page, "/transactions");
  await page.getByRole("link", { name: /^Modifica: / }).first().click();
  await page.getByRole("heading", { level: 1, name: "Modifica transazione" }).waitFor();
  await page.getByRole("button", { name: "Elimina…" }).waitFor();
  await page.waitForLoadState("networkidle");
  await shoot(page, `${tag}-full-tx-edit`, true);
  await page.getByRole("button", { name: "Elimina…" }).click();
  await page.getByRole("dialog").waitFor();
  await page.waitForLoadState("networkidle");
  await shoot(page, `${tag}-tx-delete`);
  await page.getByRole("button", { name: "Annulla" }).click();
}

/** The pages of FULL, top to bottom; a holding's page reached from the holdings list. */
async function fullShots(page, tag) {
  for (const [name, target] of FULL) {
    if (target?.startsWith("/")) {
      await open(page, target);
    } else {
      await open(page, "/holdings");
      await page.getByRole("link", { name: target ?? LONG_NAME, exact: true }).click();
      await page.getByRole("heading", { level: 1, name: target ?? LONG_NAME }).waitFor();
      // Its transactions and income load after the page: wait until they are drawn.
      await page
        .getByRole("region", { name: "Transazioni di questo titolo" })
        .getByRole("table")
        .waitFor();
      await page.waitForLoadState("networkidle");
      await page.locator(".loading").waitFor({ state: "detached" });
    }
    await shoot(page, `${tag}-full-${name}`, true);
  }
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

/** The card named `name` at the top of the window. */
async function toCard(page, name) {
  const card = page.getByRole("region", { name });
  await card.evaluate((element) => element.scrollIntoView({ block: "start" }));
  return card;
}

/**
 * Administration as the administrator (D-190): the top, the Security card, the price sources,
 * the new user dialog and the Security card's confirmation; on phones also the Security card
 * with its errors. Nothing is saved: each dialog is closed with Annulla.
 */
async function adminShots(page, tag, size) {
  await open(page, "/admin");
  await shoot(page, `${tag}-admin`);
  const security = await toCard(page, "Sicurezza");
  await shoot(page, `${tag}-admin-security`);
  await toCard(page, "Fonti prezzi");
  await shoot(page, `${tag}-admin-price-sources`);

  await page.evaluate(() => window.scrollTo(0, 0));
  await page.getByRole("button", { name: "Nuovo utente" }).click();
  await page.getByRole("dialog", { name: "Nuovo utente" }).waitFor();
  await shoot(page, `${tag}-admin-new-user`);
  await page.getByRole("button", { name: "Annulla" }).click();

  const idle = security.getByLabel("Giorni senza utilizzo");
  const before = await idle.inputValue();
  const save = security.getByRole("button", { name: "Salva" });
  if (size === "phone") {
    await idle.fill("31");
    await security.getByLabel("Primo blocco (minuti)").fill("0");
    await save.click();
    await toCard(page, "Sicurezza");
    await shoot(page, `${tag}-admin-security-errors`);
  }
  await idle.fill(before === "2" ? "3" : "2");
  await security.getByLabel("Primo blocco (minuti)").fill("1");
  await save.click();
  await page.getByRole("dialog").waitFor();
  await shoot(page, `${tag}-admin-security-confirm`);
  await page.getByRole("button", { name: "Annulla" }).click();
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

const ONLY_TRANSACTIONS = process.argv.includes("--only=transactions");
const ONLY_IMPORT = process.argv.includes("--only=import");

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
      if (user === DEMO) {
        await freshPrices(page.request);
        await addLongName(page.request);
      }
      await context.close();
    }

    for (const size of ["phone", "tablet"]) {
      for (const appearance of ["light", "dark"]) {
        const tag = `${size}-${appearance}`;
        const demo = await session(browser, DEMO, size, appearance);
        if (ONLY_IMPORT) {
          await importShots(demo.page, tag);
          await demo.context.close();
          continue;
        }
        await transactionShots(demo.page, tag);
        if (ONLY_TRANSACTIONS) {
          await demo.context.close();
          continue;
        }
        for (const [name, path] of PAGES) {
          await open(demo.page, path);
          await shoot(demo.page, `${tag}-${name}`);
        }
        await fullShots(demo.page, tag);
        await open(demo.page, "/");
        await openMenu(demo.page);
        await shoot(demo.page, `${tag}-menu`);
        // Last: its summary saves an invented CSV on this copy.
        await importShots(demo.page, tag);
        await demo.context.close();

        const admin = await session(browser, ADMIN, size, appearance);
        await adminShots(admin.page, tag, size);
        await openMenu(admin.page);
        await shoot(admin.page, `${tag}-admin-menu`);
        await admin.context.close();
      }
    }

    if (ONLY_TRANSACTIONS) return;
    // The confirmation bar stuck at 320 px (D-193).
    const narrow = await session(browser, DEMO, "narrow", "light");
    await open(narrow.page, "/import");
    await chooseFile(narrow.page, DIRECTA);
    await narrow.page.getByLabel("Importa nel conto").selectOption({ label: "Conto titoli" });
    await narrow.page.getByRole("button", { name: "Continua", exact: true }).click();
    await previewReady(narrow.page);
    await chooseTypes(narrow.page);
    await stuckBar(narrow.page);
    await shoot(narrow.page, "narrow-light-preview-stuck");
    await narrow.context.close();

    if (ONLY_IMPORT) return;
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
