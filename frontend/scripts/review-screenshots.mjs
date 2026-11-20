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
// Add holdings, search and manual prices (D-194): add holdings with two rows (one a bond), the
// same refused by the server (a cost too large to store; nothing is saved), the "Nuovo titolo…"
// dialog, one holding's manual prices, and the page of every manual price with a row refused
// because its security was deleted meanwhile (a security the script adds and deletes on this
// copy). The search results and the create form with the price check ("found" and "no
// source") come from the test-only launcher of fake providers (backend/tests/
// e2e_market_server.py: invented ISINs only, no call leaves the machine) on :8717, in its own
// throwaway folder; one investor per size and theme, for its search and check limits. Also
// add holdings at 320 px. `--only=add` takes only these.
// The remaining screens (D-195, scripts/review-remaining.mjs): accounts with their dialogs,
// Settings (top, security, sessions, the password and 2FA dialogs), About with its release
// notes, not found; per size and theme a fresh investor for the login, the forced password
// change, the 2FA recovery codes and the login's code step; on a second throwaway server,
// empty (:8718, frontend/.data-review-empty/, stopped and deleted at the end, on a crash or an
// interruption), the first run's steps and the forced enrolment. Also the bottom bar, the
// menu, Settings and the login at 320 px. `--only=rest` takes only these.
// The price-sources page (D-196, scripts/review-sources.mjs): on its own fake market server
// (:8719, frontend/.data-review-sources/, stopped and deleted at the end, on a crash or an
// interruption), at 1440x900, 820x1180 and 390x844 in light Moderno and dark, plus 320 px: the
// page, an engine on and off, the orders, the exchanges and an error, the confirmation, the
// run's answers, a mocked paused engine, the Administration card with its link; with market
// data off on the demo server. `--only=sources` takes only these.
import { spawn } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";

import { chromium } from "@playwright/test";

import { ADMIN, CSRF, DEMO, FRONTEND, freshPrices, portFree, ready, startDemo, stop } from "./demo-server.mjs";
import {
  EMPTY_BASE,
  EMPTY_DATA,
  emptyAdministrators,
  firstRunShots,
  forcedEnrolmentShots,
  investorSignInShots,
  narrowShots,
  remainingShots,
  startEmpty,
} from "./review-remaining.mjs";
import {
  narrowSourcesShots,
  offShot,
  runningShot,
  SOURCES_BASE,
  SOURCES_DATA,
  sourcesAdministrators,
  sourcesSession,
  sourcesShots,
  startSources,
} from "./review-sources.mjs";

const DATA = `${FRONTEND}/.data-review`; // ignored by git (.data*/)
const OUT = `${FRONTEND}/.review-screenshots`; // ignored by git
const PORT = 8716;
const BASE = `http://127.0.0.1:${PORT}`;
// The fake market server (D-194): its own throwaway folder, named .data… as the launcher asks.
const MARKET_DATA = `${FRONTEND}/.data-review-market`; // ignored by git (.data*/)
const MARKET_PORT = 8717;
const MARKET_BASE = `http://127.0.0.1:${MARKET_PORT}`;
// Invented credentials and ISINs of the launcher's fakes (fake_isin in backend/tests/helpers.py).
const MARKET_ADMIN = { username: "review-admin", password: "review market password 0001" };
const WORLD_ISIN = "ZZ0000087013"; // three listings: Milan and XETRA in EUR, London in pence
const BOND_ISIN = "ZZ0000087039"; // no fake provider knows it

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

/** Add holdings (screen 10) and the manual prices (screen 12) as the demo investor (D-194).
 * Nothing is saved but the security added, then deleted, for the refused row. */
async function addShots(page, tag) {
  const assets = await (await page.request.get("/api/v1/assets")).json();
  const id = (name) => assets.find((a) => a.name === name).id;
  await open(page, "/holdings/existing");
  await page.getByLabel("Conto", { exact: true }).selectOption({ label: "Conto titoli (EUR)" });
  await page.getByLabel("Titolo, riga 1").selectOption(id("Esempio Azionario Mondo ETF"));
  await page.getByLabel("Quantità, riga 1").fill("12");
  await page.getByLabel("Prezzo medio di carico, riga 1").fill("96,40");
  await page.getByLabel("Titolo, riga 2").selectOption(id("Esempio Titolo di Stato 2031"));
  await page.getByLabel("Nominale, riga 2").fill("5000");
  await page.getByLabel("Prezzo medio di carico, riga 2").fill("98,5");
  await page.locator("table.add-rows tbody tr").nth(1).getByText("4.925,00 €").waitFor();
  await toHeading(page, "Aggiungi titoli che già possiedo");
  await shoot(page, `${tag}-add-holdings`);
  await shoot(page, `${tag}-full-add-holdings`, true);
  // Refused by the server's dry run: too large a cost to store (D-98).
  await page.getByLabel("Nominale, riga 2").fill("999999999999999");
  await page.getByLabel("Prezzo medio di carico, riga 2").fill("999999999999999");
  await page.getByRole("button", { name: /^Salva \d+ titol/ }).click();
  await page.getByRole("alert").filter({ hasText: "Controlla la riga 2." }).waitFor();
  await page.locator("table.add-rows tbody tr").nth(1).evaluate((row) => row.scrollIntoView({ block: "start" }));
  await shoot(page, `${tag}-add-holdings-error`);
  await shoot(page, `${tag}-full-add-holdings-error`, true);
  await page.getByRole("button", { name: "Nuovo titolo per la riga 3" }).click();
  await page.getByRole("dialog", { name: "Aggiungi un titolo" }).waitFor();
  await shoot(page, `${tag}-add-holdings-new-security`);
  await page.getByRole("dialog").getByRole("button", { name: "Annulla" }).click();

  await open(page, `/holdings/${id("Esempio Azionario Mondo ETF")}/prices`);
  await shoot(page, `${tag}-manual-prices`);
  await shoot(page, `${tag}-full-manual-prices`, true);

  // A held security deleted "in another tab" while the page is open: the server refuses its row.
  const accounts = await (await page.request.get("/api/v1/accounts")).json();
  const account = accounts.find((a) => a.currency === "EUR");
  const made = await page.request.post("/api/v1/assets", {
    data: { name: "Esempio Titolo Eliminato", asset_type: "STOCK", quote_currency: "EUR", quote_unit: "CURRENCY" },
    headers: CSRF,
  });
  if (!made.ok()) throw new Error(`security refused (${made.status()})`);
  const asset = (await made.json()).id;
  const bought = await page.request.post("/api/v1/transactions", {
    data: {
      account_id: account.id, type: "BUY", trade_date: "2026-03-02", asset_id: asset, quantity: "5",
      unit_price: "10", gross_amount: "50.00", total_amount: "50.00", affects_cash: false,
    }, // prettier-ignore
    headers: CSRF,
  });
  if (!bought.ok()) throw new Error(`purchase refused (${bought.status()})`);
  const purchase = (await bought.json()).transactions[0].id;
  await open(page, "/holdings/prices");
  await page.getByRole("link", { name: "Esempio Titolo Eliminato" }).waitFor();
  await page.request.delete(`/api/v1/transactions/${purchase}`, { headers: CSRF });
  await page.request.delete(`/api/v1/assets/${asset}`, { headers: CSRF });
  await page.getByLabel("Esempio Titolo Eliminato: Prezzo (EUR)").fill("11,20");
  await page.getByRole("button", { name: "Salva i prezzi" }).click();
  await page.getByText("Non trovato: potrebbe essere stato eliminato nel frattempo.").waitFor();
  await shoot(page, `${tag}-manual-prices-all-error`);
  await shoot(page, `${tag}-full-manual-prices-all-error`, true);
}

/** Starts the test-only launcher of fake providers on a fresh folder (D-194, as e2e/serve.sh
 * market): invented ISINs only, no outbound call. */
async function startMarket() {
  await portFree(MARKET_PORT);
  rmSync(MARKET_DATA, { recursive: true, force: true });
  mkdirSync(MARKET_DATA, { recursive: true });
  const backend = `${FRONTEND}/../backend`;
  const server = spawn(`${backend}/.venv/bin/python`, ["tests/e2e_market_server.py"], {
    cwd: backend,
    env: {
      ...process.env,
      PT_DATA_DIR: MARKET_DATA,
      PT_HOST: "127.0.0.1",
      PT_PORT: String(MARKET_PORT),
      PT_STATIC_DIR: `${FRONTEND}/dist`,
      PT_MARKET_DATA: "false",
      PT_COOKIE_SECURE: "false",
      PT_REQUIRE_2FA: "off",
    },
    stdio: "ignore",
  });
  console.log(`market server started, PID ${server.pid}`);
  // Stopped by main's finally; also when this script is interrupted or exits on a crash.
  const kill = () => {
    if (server.exitCode === null && server.signalCode === null) server.kill("SIGTERM");
  };
  process.once("exit", kill);
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      kill();
      rmSync(MARKET_DATA, { recursive: true, force: true });
      process.exit(130);
    });
  }
  return server;
}

/** The market server's administrator, then a new Italian investor with the Moderno theme. */
async function marketInvestor(browser, serial) {
  const context = await browser.newContext({ baseURL: MARKET_BASE });
  const api = context.request;
  if (serial === 0) {
    const created = await api.post("/api/v1/setup/admin", {
      data: { ...MARKET_ADMIN, first_name: "Rita", last_name: "Esempio", email: "review-admin@example.org", language: "it" },
      headers: CSRF,
    });
    if (created.status() !== 201) throw new Error(`market administrator refused (${created.status()})`);
  } else {
    const login = await api.post("/api/v1/auth/login", { data: MARKET_ADMIN, headers: CSRF });
    if (!login.ok()) throw new Error(`market administrator login failed (${login.status()})`);
  }
  const username = `revisione-${serial}`;
  const user = await api.post("/api/v1/admin/users", {
    data: { username, language: "it", role: "INVESTOR", first_name: "Elena", last_name: "Esempio", email: `${username}@example.org` },
    headers: CSRF,
  });
  if (!user.ok()) throw new Error(`market investor refused (${user.status()})`);
  const { temporary_password } = await user.json();
  await api.post("/api/v1/auth/logout", { headers: CSRF });
  await api.post("/api/v1/auth/login", { data: { username, password: temporary_password }, headers: CSRF });
  const password = "review investor password 0001";
  await api.post("/api/v1/me/password", {
    data: { current_password: temporary_password, new_password: password },
    headers: CSRF,
  });
  await api.patch("/api/v1/me", { data: { theme: "moderno" }, headers: CSRF });
  await api.post("/api/v1/accounts", { data: { name: "Conto esempio", currency: "EUR" }, headers: CSRF });
  await context.close();
  return { username, password };
}

/** The search and the create form with the price check, on the fake market server. Nothing is
 * saved. */
async function searchShots(page, tag) {
  await open(page, "/holdings/add");
  await page.getByLabel("Cerca per ISIN, nome o simbolo").fill(WORLD_ISIN);
  await page.getByRole("button", { name: "Cerca", exact: true }).click();
  await page.getByText("3 risultati").waitFor();
  await shoot(page, `${tag}-search-results`);
  await shoot(page, `${tag}-full-search-results`, true);
  await page.getByRole("button", { name: /^Aggiungi «.+» \(ZWLD\.MI, EUR\)$/ }).click();
  const check = page.getByRole("button", { name: "Controlla la fonte prezzi" });
  await check.click();
  await page.locator(".price-check-result").getByText(/^Fonte prevista/).waitFor();
  await check.evaluate((button) => button.scrollIntoView({ block: "center" }));
  await shoot(page, `${tag}-create-found`);
  await shoot(page, `${tag}-full-create-found`, true);
  await page.getByLabel("Tipo").selectOption("BOND");
  await page.getByLabel("ISIN (facoltativo)").fill(BOND_ISIN);
  await check.click();
  await page.locator(".price-check-result").getByText(/^Nessuna fonte/).waitFor();
  await check.evaluate((button) => button.scrollIntoView({ block: "center" }));
  await shoot(page, `${tag}-create-no-source`);
}

/** A browser context of the given size and appearance, logged in as `user`. */
async function session(browser, user, size, appearance, base = BASE) {
  const context = await browser.newContext({
    baseURL: base,
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
const ONLY_ADD = process.argv.includes("--only=add");
const ONLY_REST = process.argv.includes("--only=rest");
const ONLY_SOURCES = process.argv.includes("--only=sources");

/** A browser of the given size and appearance, in Italian, without a session. */
async function bare(browser, size, appearance, base = BASE) {
  const context = await browser.newContext({
    baseURL: base,
    viewport: SIZES[size],
    hasTouch: size !== "tablet",
    colorScheme: appearance === "dark" ? "dark" : "light",
    locale: "it-IT",
    timezoneId: "UTC",
  });
  return { context, page: await context.newPage() };
}

/** The remaining screens of one size and theme on the demo server (D-195). */
async function restShots(browser, demo, size, appearance, tag, serial) {
  await remainingShots(demo.page, tag, shoot);
  const make = () => bare(browser, size, appearance);
  await investorSignInShots(browser, { base: BASE, make }, tag, serial, shoot);
}

async function main() {
  const server = await startDemo(DATA, PORT);
  let market = null; // stopped in the finally below, whatever happens
  let empty = null; // the same
  let sources = null; // the same
  let browser = null;
  const withEmpty = !(ONLY_TRANSACTIONS || ONLY_IMPORT || ONLY_ADD || ONLY_SOURCES);
  const withSources = ONLY_SOURCES || !(ONLY_TRANSACTIONS || ONLY_IMPORT || ONLY_ADD || ONLY_REST);
  try {
    if (!ONLY_SOURCES) market = await startMarket();
    if (withEmpty) empty = await startEmpty();
    if (withSources) sources = await startSources();
    browser = await chromium.launch();
    await ready(server, BASE);
    if (market) await ready(market, MARKET_BASE);
    if (empty) await ready(empty, EMPTY_BASE);
    if (sources) await ready(sources, SOURCES_BASE);
    rmSync(OUT, { recursive: true, force: true });
    mkdirSync(OUT, { recursive: true });

    if (sources) {
      // The price-sources page (D-196): one administrator per size and theme on its own server.
      const looks = [];
      for (const size of ["desktop", "tablet", "phone"]) {
        for (const appearance of ["light", "dark"]) looks.push([size, appearance]);
      }
      const admins = await sourcesAdministrators(browser, [...looks.map(([a, b]) => `${a}-${b}`), "narrow"]);
      for (const [index, [size, appearance]] of looks.entries()) {
        const tag = `${size}-${appearance}`;
        const admin = await sourcesSession(browser, admins[index], size, appearance);
        if (index === 0) await runningShot(admin.page, tag, shoot);
        await sourcesShots(admin.page, tag, shoot);
        await admin.context.close();
        const off = await sourcesSession(browser, ADMIN, size, appearance, BASE);
        await offShot(off.page, tag, shoot);
        await off.context.close();
      }
      const tight = await sourcesSession(browser, admins[looks.length], "narrow", "light");
      await narrowSourcesShots(tight.page, shoot);
      await tight.context.close();
    }
    if (ONLY_SOURCES) return;

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

    let serial = 0; // one market investor per size and theme
    for (const size of ["phone", "tablet"]) {
      for (const appearance of ["light", "dark"]) {
        const tag = `${size}-${appearance}`;
        const demo = await session(browser, DEMO, size, appearance);
        if (ONLY_REST) {
          await restShots(browser, demo, size, appearance, tag, serial++);
          await demo.context.close();
          continue;
        }
        if (ONLY_ADD) {
          await addShots(demo.page, tag);
          await demo.context.close();
          const investor = await marketInvestor(browser, serial++);
          const shopper = await session(browser, investor, size, appearance, MARKET_BASE);
          await searchShots(shopper.page, tag);
          await shopper.context.close();
          continue;
        }
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
        await addShots(demo.page, tag);
        await restShots(browser, demo, size, appearance, tag, serial);
        const investor = await marketInvestor(browser, serial++);
        const shopper = await session(browser, investor, size, appearance, MARKET_BASE);
        await searchShots(shopper.page, tag);
        await shopper.context.close();
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
    if (empty) {
      // The empty server (D-195): the first run in every size and theme, never submitted;
      // then one administrator per size and theme through the forced enrolment.
      const tags = [];
      for (const size of ["phone", "tablet"]) {
        for (const appearance of ["light", "dark"]) {
          const fresh = await bare(browser, size, appearance, EMPTY_BASE);
          await firstRunShots(fresh.page, `${size}-${appearance}`, shoot);
          await fresh.context.close();
          tags.push([size, appearance]);
        }
      }
      const admins = await emptyAdministrators(browser, tags.map(([size, look]) => `${size}-${look}`));
      for (const [index, [size, appearance]] of tags.entries()) {
        const fresh = await bare(browser, size, appearance, EMPTY_BASE);
        await forcedEnrolmentShots(fresh.page, `${size}-${appearance}`, admins[index], shoot);
        await fresh.context.close();
      }
      // The bar, the menu, Settings and the login at 320 px.
      const tight = await session(browser, DEMO, "narrow", "light");
      await narrowShots(tight.page, tight.context, shoot);
      await tight.context.close();
    }
    if (ONLY_REST) return;
    // Add holdings at 320 px, with the refused row (D-194).
    const small = await session(browser, DEMO, "narrow", "light");
    await addNarrow(small.page);
    await small.context.close();
    if (ONLY_ADD) return;
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
    await browser?.close();
    await stop(server);
    console.log(`server stopped, PID ${server.pid}`);
    if (market) {
      await stop(market);
      console.log(`market server stopped, PID ${market.pid}`);
    }
    if (empty) {
      await stop(empty);
      console.log(`empty server stopped, PID ${empty.pid}`);
    }
    if (sources) {
      await stop(sources);
      console.log(`price-sources server stopped, PID ${sources.pid}`);
    }
    rmSync(DATA, { recursive: true, force: true });
    rmSync(MARKET_DATA, { recursive: true, force: true });
    rmSync(EMPTY_DATA, { recursive: true, force: true });
    rmSync(SOURCES_DATA, { recursive: true, force: true });
  }
}

/** Add holdings at 320 px: a bond row and a row refused by the server (D-194). */
async function addNarrow(page) {
  const assets = await (await page.request.get("/api/v1/assets")).json();
  const id = (name) => assets.find((a) => a.name === name).id;
  await open(page, "/holdings/existing");
  await page.getByLabel("Conto", { exact: true }).selectOption({ label: "Conto titoli (EUR)" });
  await page.getByLabel("Titolo, riga 1").selectOption(id("Esempio Azionario Mondo ETF"));
  await page.getByLabel("Quantità, riga 1").fill("12");
  await page.getByLabel("Prezzo medio di carico, riga 1").fill("999999999999999");
  await page.getByLabel("Titolo, riga 2").selectOption(id("Esempio Titolo di Stato 2031"));
  await page.getByLabel("Nominale, riga 2").fill("5000");
  await page.getByLabel("Prezzo medio di carico, riga 2").fill("98,5");
  await page.getByRole("button", { name: /^Salva \d+ titol/ }).click();
  await page.getByRole("alert").filter({ hasText: "Controlla la riga 1." }).waitFor();
  await toHeading(page, "Aggiungi titoli che già possiedo");
  await shoot(page, "narrow-light-add-holdings-error");
  await shoot(page, "narrow-light-full-add-holdings-error", true);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
