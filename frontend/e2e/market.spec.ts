import { writeFileSync } from "node:fs";

import { expect, type Page, request, test } from "@playwright/test";

import { MARKET } from "../playwright.config";
import { apiPost, CSRF, logIn, type User } from "./helpers";

// The search page and automatic prices against the fake price source of the market server
// (backend/tests/e2e_market_server.py, invented ISINs and closes only; no call leaves the
// machine). Its folder is fresh at each run: the first test of this file creates its
// Administrator. WORLD (gated) is added once per run; at most 5 searches per run (D-128).

const WORLD_ISIN = "ZZ0000087013"; // fake_isin(8701): Milan and XETRA in EUR, London in pence
const STOCK_ISIN = "ZZ0000087021"; // fake_isin(8702): Milan in EUR only
const WORLD = "Example Market World UCITS ETF";
const ADMIN: User = { username: "market-admin", password: "e2e market password 0001" };

type Created = { id: string };
type Position = { asset_id: string; price: string | null; price_date: string | null; price_status: string };
type Asset = { id: string; isin: string | null; ticker: string | null; name: string };

test.use({ baseURL: MARKET });
test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  const api = await request.newContext({ baseURL: MARKET, extraHTTPHeaders: CSRF });
  const created = await api.post("/api/v1/setup/admin", { data: { ...ADMIN, language: "en" } });
  expect(created.status(), "the market server's administrator").toBe(201);
  await api.dispose();
});

async function account(page: Page, name: string, currency = "EUR") {
  return apiPost<Created>(page, "/accounts", { name, currency });
}

const positions = async (page: Page, accountId: string) =>
  (await (await page.request.get(`/api/v1/accounts/${accountId}/positions`)).json()) as Position[];

test("search by ISIN: one row per currency, add, looked up, then the price shown", async ({
  page,
}, testInfo) => {
  await logIn(page, ADMIN);
  const conto = await account(page, "Conto mercato");
  await page.goto(`/holdings/add?account=${conto.id}`);
  await page.getByLabel("Search by ISIN, name or symbol").fill(WORLD_ISIN);
  await page.getByLabel("Search by ISIN, name or symbol").press("Enter");
  const eur = page.getByRole("button", { name: `Add “${WORLD}” (EUR)` });
  await expect(eur).toBeVisible();
  await expect(page.getByText("2 results")).toBeVisible();
  await expect(page.locator("li", { has: eur })).toContainText("Exchanges: Milan, XETRA");
  const gbp = page.getByRole("button", { name: `Add “${WORLD}” (GBP)` });
  await expect(page.locator("li", { has: gbp })).toContainText("Exchanges: London");
  await expect(page.getByText(/Not in your currency\?/)).toHaveCount(0);

  // "Add" fills the form in; the name can be changed before anything is stored.
  await eur.click();
  const name = page.getByLabel("Name", { exact: true });
  await expect(name).toBeFocused();
  await expect(name).toHaveValue(WORLD);
  await expect(page.getByText("Check the name: it comes from the price source")).toBeVisible();
  await expect(page.getByLabel("ISIN (optional)")).toHaveValue(WORLD_ISIN);
  await name.fill("Example World ETF");
  await page.getByRole("button", { name: "Create security" }).click();
  await expect(
    page.getByText("“Example World ETF” added. Automatic prices are being looked up in the background."),
  ).toBeVisible();

  // Held already: screen 10 from the notice, with the account and the security chosen.
  await page.getByRole("link", { name: "Add holdings I already own" }).click();
  await page.getByLabel("Quantity, row 1").fill("10");
  await page.getByLabel("Average cost, row 1").fill("100");
  await page.getByRole("button", { name: "Save 1 security" }).click();
  await expect(page.getByText("1 security added to “Conto mercato”.")).toBeVisible();
  const row = page.getByRole("row", { name: /Example World ETF/ });
  await expect(row.getByText("Looking up prices")).toBeVisible(); // the gate holds the job
  const [held] = await positions(page, conto.id);
  expect(held).toMatchObject({ price_status: "PENDING", price: null });

  // The gate opens: the job finds Milan's closes; wait for the price, not for OK.
  writeFileSync(`${testInfo.config.rootDir}/../.data-e2e-market/gate-ZWLD.MI`, "open");
  await expect
    .poll(async () => (await positions(page, conto.id))[0].price, { timeout: 30_000 })
    .toBe("101.75");
  await row.getByRole("link", { name: "Example World ETF" }).click();
  await expect(page).toHaveURL(new RegExp(`/holdings/${held.asset_id}`)); // then reload it
  await page.reload();
  await expect(page.getByText("Automatic prices, updated daily.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Position value" })).toContainText("€1,017.50");
});

test("a text search gives a security with prices by hand only", async ({ page }) => {
  await logIn(page, ADMIN);
  const conto = await account(page, "Conto testo");
  await page.goto(`/holdings/add?account=${conto.id}`);
  await page.getByLabel("Search by ISIN, name or symbol").fill("example text");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  const add = page.getByRole("button", { name: "Add “Example Market Text Holding” (EUR)" });
  await expect(page.locator("li", { has: add })).toContainText(
    "No ISIN: manual prices only. Search by ISIN for automatic prices.",
  );
  await add.click();
  await expect(page.getByLabel("Symbol (optional)")).toHaveValue("ZTXT.MI");
  await expect(page.getByLabel("ISIN (optional)")).toHaveValue("");
  await page.getByRole("button", { name: "Create security" }).click();
  await expect(
    page.getByText("“Example Market Text Holding” added. No ISIN: you enter its prices yourself."),
  ).toBeVisible();

  const assets = (await (await page.request.get("/api/v1/assets")).json()) as Asset[];
  const text = assets.find((a) => a.ticker === "ZTXT.MI")!;
  expect(text.isin).toBeNull();
  await apiPost(page, "/transactions/batch", {
    transactions: [
      {
        account_id: conto.id, type: "BUY", trade_date: "2026-01-05", asset_id: text.id,
        quantity: "5", unit_price: "12", gross_amount: "60", total_amount: "60", affects_cash: false,
      },
    ],
  }); // prettier-ignore
  expect((await positions(page, conto.id))[0].price_status).toBe("MANUAL");
});

test("the currency offer: an ISIN found only in euros, from a dollar account", async ({ page }) => {
  await logIn(page, ADMIN);
  const dollars = await account(page, "Conto dollari", "USD");
  await page.goto(`/holdings/add?account=${dollars.id}`);
  await page.getByLabel("Search by ISIN, name or symbol").fill(STOCK_ISIN);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("1 result")).toBeVisible();
  await expect(
    page.getByText("Not in your currency? Create it by hand with this ISIN in USD."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Create in USD" }).click();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("Example Market Industrial SpA");
  await expect(page.getByLabel("Currency")).toHaveValue("USD");
  await expect(page.getByLabel("ISIN (optional)")).toHaveValue(STOCK_ISIN);
  await expect(page.getByLabel("Type")).toHaveValue("STOCK");
});

test("a refused search: too many searches, and the price source unavailable", async ({ page }) => {
  await logIn(page, ADMIN);
  const replies: { status: number; headers: Record<string, string>; body: object }[] = [
    { status: 429, headers: { "Retry-After": "30" }, body: { code: "TOO_MANY_ATTEMPTS" } },
    { status: 503, headers: {}, body: { code: "MARKET_DATA_UNAVAILABLE" } },
  ];
  await page.route("**/api/v1/assets/search", (route) => {
    const reply = replies.shift()!;
    return route.fulfill({
      status: reply.status,
      headers: reply.headers,
      contentType: "application/json",
      body: JSON.stringify(reply.body),
    });
  });
  await page.goto("/holdings/add");
  await page.getByLabel("Search by ISIN, name or symbol").fill(WORLD_ISIN);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("Too many attempts. Try again in 1 minute.")).toBeVisible();
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(
    page.getByText("The price source cannot answer now. Try again later, or create the security by hand."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Create security" })).toBeEnabled();
});

test("a manual price in use names no exchange (D-152)", async ({ page }) => {
  await logIn(page, ADMIN);
  const conto = await account(page, "Conto prezzi");
  const stock = await apiPost<Created>(page, "/assets", {
    name: "Example Industrial", asset_type: "STOCK", quote_currency: "EUR",
    quote_unit: "CURRENCY", isin: STOCK_ISIN,
  }); // prettier-ignore
  await apiPost(page, "/transactions/batch", {
    transactions: [
      {
        account_id: conto.id, type: "BUY", trade_date: "2026-01-05", asset_id: stock.id,
        quantity: "10", unit_price: "20", gross_amount: "200", total_amount: "200", affects_cash: false,
      },
    ],
  }); // prettier-ignore
  await expect
    .poll(async () => (await positions(page, conto.id))[0].price, { timeout: 30_000 })
    .not.toBeNull();
  await page.goto(`/holdings/${stock.id}?account=${conto.id}`);
  const value = page.getByRole("region", { name: "Position value" });
  await expect(value).toContainText("Price from: Milan · close of");

  await page.getByRole("link", { name: "Manual prices", exact: true }).click();
  await page.getByLabel("Price (EUR)").fill("26");
  await page.getByRole("button", { name: "Save price" }).click();
  await expect(page.getByText(/^Price in use: €26\.00 on .* \(manual\)$/)).toBeVisible();
  await page.getByRole("link", { name: "← Example Industrial" }).click();
  await expect(value).toContainText("€260.00");
  await expect(value).not.toContainText("Price from");
  await expect(page.getByText("Manual price", { exact: true })).toBeVisible();
});
