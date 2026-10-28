import { expect, type Page, test } from "@playwright/test";

import { apiPost, DEMO, logIn, newUser } from "./helpers";

// Invented accounts, securities and amounts only. The end-to-end servers run with market
// data off (PT_MARKET_DATA=false), so the holdings say automatic prices are off.

type Created = { id: string };

const names = (page: Page) =>
  page.locator("table.holdings tbody tr th a").allTextContents();

test("the seeded holdings: all accounts in euros, one account in its currency, filters", async ({
  page,
}) => {
  await logIn(page, DEMO);
  // The menu's link: the dashboard banner's "e altri N titoli: vedi Titoli" would match too.
  await page
    .getByRole("navigation", { name: "Menu principale" })
    .getByRole("link", { name: "Titoli" })
    .click();
  await expect(
    page.getByText("Vista: tutti i conti (2) · valori in EUR · 5 posizioni aperte"),
  ).toBeVisible();
  expect(await names(page)).toEqual([
    "Esempio Azionario Mondo ETF",
    "Esempio Industrie SpA",
    "Esempio Obbligazionario Euro ETF",
    "Esempio Titolo di Stato 2031",
    "Example Robotics Inc.",
  ]);
  await expect(page.getByRole("columnheader", { name: "PMC (EUR)" })).toBeVisible();

  // The dollar account's stock, at the invented rate of 1.25: 3,001.00 USD of cost = 2,400.80
  // EUR, 20 shares -> 120.04 EUR each; its price stays in dollars.
  const robotics = page.getByRole("row", { name: /Example Robotics/ });
  await expect(robotics.getByText("Conto estero")).toBeVisible();
  await expect(robotics).toContainText(/120,04\s€/);
  await expect(robotics).toContainText(/162,50\s\$/);
  await expect(robotics).toContainText(/2\.600,00\s€/);
  // Market data off is said once above the table, not on each row (D-145).
  await expect(
    page.getByText(
      "Prezzi automatici disattivati su questa installazione: i prezzi si inseriscono a mano.",
    ),
  ).toBeVisible();
  await expect(robotics.getByText("Prezzi automatici disattivati")).toHaveCount(0);
  await expect(robotics.getByText("Prezzo non aggiornato dal 30 dic 2025")).toBeVisible();
  const btp = page.getByRole("row", { name: /Titolo di Stato/ });
  await expect(btp).toContainText("98,06%"); // PMC as % of nominal
  await expect(btp).toContainText("99,15%");
  await expect(page.getByText(/convertiti in EUR al cambio più recente/)).toBeVisible();

  // One account: its own currency, no account column.
  await page.getByLabel("Conto in vista").selectOption({ label: "Conto estero" });
  await expect(page.getByText("Vista: Conto estero · valori in USD · 1 posizione aperta")).toBeVisible();
  await expect(page.getByRole("row", { name: /Example Robotics/ })).toContainText(/150,05\s\$/);
  await expect(page.getByRole("columnheader", { name: "Conto" })).toHaveCount(0);

  // Filters stay in the URL, with the account in view.
  await page.getByLabel("Conto in vista").selectOption({ label: "Conto titoli" });
  await page.getByLabel("Tipo").selectOption({ label: "Obbligazione" });
  await expect(page).toHaveURL(/type=BOND/);
  await page.reload();
  await expect(page.getByLabel("Tipo")).toHaveValue("BOND");
  expect(await names(page)).toEqual(["Esempio Titolo di Stato 2031"]);
});

test("a security without an ISIN and a closed position", async ({ page }) => {
  await logIn(page, await newUser("holdings"));
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const manual = await apiPost<Created>(page, "/assets", {
    name: "Esempio Fondo Locale",
    asset_type: "ETF",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
  });
  const sold = await apiPost<Created>(page, "/assets", {
    name: "Esempio Venduto SpA",
    asset_type: "STOCK",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
  });
  const buy = (asset: Created, quantity: string, gross: string) =>
    apiPost(page, "/transactions", {
      account_id: account.id,
      type: "BUY",
      trade_date: "2026-03-02",
      asset_id: asset.id,
      quantity,
      unit_price: "10.00",
      gross_amount: gross,
      total_amount: gross,
      affects_cash: false,
    });
  await buy(manual, "10", "100.00");
  await buy(sold, "5", "50.00");
  await apiPost(page, "/transactions", {
    account_id: account.id,
    type: "SELL",
    trade_date: "2026-04-01",
    asset_id: sold.id,
    quantity: "5",
    unit_price: "12.00",
    gross_amount: "60.00",
    total_amount: "60.00",
    affects_cash: false,
  });

  await page.goto("/holdings");
  await expect(page.getByText("View: all accounts (1) · values in EUR · 1 open position")).toBeVisible();
  const row = page.getByRole("row", { name: /Esempio Fondo Locale/ });
  await expect(row.getByText("Prices by hand only")).toBeVisible();
  await expect(row.getByText("Estimated at cost")).toBeVisible(); // no price yet (D-46)
  expect(await names(page)).toEqual(["Esempio Fondo Locale"]);

  await page.getByLabel("Show closed positions").check();
  const closed = page.getByRole("row", { name: /Esempio Venduto/ });
  await expect(closed).toContainText("closed position");
  await expect(closed).toContainText("+€10.00"); // realized 60 - 50
  await expect(page).toHaveURL(/closed=1/);
});

test("the detail: its figures, then a purchase added and one edited, both back here", async ({
  page,
}) => {
  await logIn(page, await newUser("detail"));
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const etf = await apiPost<Created>(page, "/assets", {
    name: "Esempio Mondo ETF",
    asset_type: "ETF",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
  });
  const trades = [
    { type: "DEPOSIT", trade_date: "2026-01-05", total_amount: "5000.00" },
    {
      type: "BUY", trade_date: "2026-03-02", asset_id: etf.id, quantity: "10",
      unit_price: "100.00", gross_amount: "1000.00", fees: "5.00", total_amount: "1005.00",
    },
    {
      type: "DIVIDEND", trade_date: "2026-06-01", asset_id: etf.id, gross_amount: "12.50",
      taxes: "3.25", total_amount: "9.25",
    },
  ]; // prettier-ignore
  for (const tx of trades) await apiPost(page, "/transactions", { account_id: account.id, ...tx });
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  const priced = await page.request.put(`/api/v1/assets/${etf.id}/prices`, {
    data: { day: yesterday, close: "110.00" },
    headers: { "X-Requested-With": "portfolio-tracker" },
  });
  expect(priced.ok()).toBe(true);

  await page.goto("/holdings");
  await page.getByRole("link", { name: "Esempio Mondo ETF" }).click();
  await expect(page.getByRole("heading", { name: "Esempio Mondo ETF", level: 1 })).toBeVisible();
  await expect(page.getByText("No ISIN: prices by hand only.")).toBeVisible();
  const value = page.getByRole("region", { name: "Position value" });
  await expect(value).toContainText("€1,100.00");
  await expect(value).toContainText("10 units × €110.00");
  await expect(page.getByRole("region", { name: "Cost basis" })).toContainText(
    "€1,005.00Average cost €100.50",
  );
  // 95.00 unrealized + 12.50 dividend = 107.50 gross; less 3.25 withheld = 104.25 net.
  const gain = page.getByRole("region", { name: "Net gain on this security" });
  await expect(gain).toContainText("+€104.25");
  await expect(gain).toContainText("Gross +€107.50 · taxes €3.25");
  await expect(page.getByRole("region", { name: "Dividends received" })).toContainText("€9.25 net");

  await page.getByRole("link", { name: "Add transaction" }).click();
  await expect(page.getByRole("heading", { name: "New transaction", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Security", { exact: true })).toHaveValue(etf.id);
  await page.getByLabel("Date", { exact: true }).fill("01/07/2026");
  await page.getByLabel("Quantity").fill("5");
  await page.getByLabel("Unit price (EUR)").fill("104");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Transaction saved." })).toBeVisible();
  await expect(page).toHaveURL(`/holdings/${etf.id}`);
  const txs = page.getByRole("region", { name: "Transactions of this security" });
  await expect(txs.getByRole("row")).toHaveCount(4); // the header, two purchases, the dividend

  await txs.getByRole("link", { name: "Edit: Buy, Esempio Mondo ETF, Jul 1, 2026" }).click();
  await expect(page.getByRole("heading", { name: "Edit transaction", level: 1 })).toBeVisible();
  await page.getByLabel("Quantity").fill("6");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Changes saved." })).toBeVisible();
  await expect(page).toHaveURL(`/holdings/${etf.id}`);
  await expect(page.getByRole("region", { name: "Position value" })).toContainText(
    "16 units × €110.00",
  );
});

test("delete all of a security's transactions, from its detail, in one account", async ({
  page,
}) => {
  await logIn(page, await newUser("delete-all"));
  const first = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const second = await apiPost<Created>(page, "/accounts", { name: "Altro", currency: "EUR" });
  const stock = await apiPost<Created>(page, "/assets", {
    name: "Esempio Industrie SpA",
    asset_type: "STOCK",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
  });
  const buy = { type: "BUY", trade_date: "2026-03-03", asset_id: stock.id, quantity: "20",
                unit_price: "10", gross_amount: "200.00", total_amount: "200.00",
                affects_cash: false }; // prettier-ignore
  await apiPost(page, "/transactions", { account_id: first.id, ...buy });
  await apiPost(page, "/transactions", {
    account_id: first.id, type: "DIVIDEND", trade_date: "2026-06-01", asset_id: stock.id,
    gross_amount: "10.00", taxes: "2.60", total_amount: "7.40", affects_cash: false,
  }); // prettier-ignore
  await apiPost(page, "/transactions", { account_id: second.id, ...buy });

  // Over all accounts there is no account to delete from.
  await page.goto(`/holdings/${stock.id}`);
  await expect(page.getByRole("heading", { name: "Esempio Industrie SpA", level: 1 })).toBeVisible();
  const deleteAll = page.getByRole("button", { name: "Delete all transactions of this security…" });
  await expect(page.getByRole("region", { name: "Transactions of this security" })).toBeVisible();
  await expect(deleteAll).toHaveCount(0);

  await page.getByLabel("Account in view").selectOption({ label: "Conto" });
  await deleteAll.click();
  const dialog = page.getByRole("dialog", { name: "Delete all transactions of a security?" });
  await expect(dialog).toContainText("Esempio Industrie SpA · Conto · 2 transactions.");
  const submit = dialog.getByRole("button", { name: "Delete 2 transactions" });
  await expect(submit).toBeDisabled();
  await dialog.getByLabel("Type the name of the security to confirm").fill("Esempio Industrie SpA");
  await submit.click();
  await expect(page.getByRole("status").filter({ hasText: "2 transactions deleted." })).toBeVisible();
  await expect(page).toHaveURL(`/holdings?account=${first.id}`);
  await expect(page.getByText("No securities in this view")).toBeVisible();

  // The other account still holds it.
  await page.getByLabel("Account in view").selectOption({ label: "All accounts" });
  const row = page.getByRole("row", { name: /Esempio Industrie/ });
  await expect(row.getByText("Altro")).toBeVisible();
  await expect(row.getByText("Conto", { exact: true })).toHaveCount(0);
});
