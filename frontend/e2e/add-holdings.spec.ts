import { expect, test } from "@playwright/test";

import { apiPost, logIn, newUser } from "./helpers";

// Invented accounts, securities, ISINs and amounts only. The seeded server runs with market
// data off (PT_MARKET_DATA=false): the search page says so and never asks the server.

type Created = { id: string };
type Asset = { id: string; name: string; isin: string | null; quote_currency: string };

const ISIN = "ZZ0000087039"; // invented, valid check digit

test("the search page with market data off: no search sent, a security created by hand", async ({
  page,
}) => {
  await logIn(page, await newUser("search-off"));
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const searches: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/assets/search")) searches.push(request.url());
  });

  await page.goto(`/holdings/add?account=${account.id}`);
  await expect(page.getByRole("heading", { name: "Add a security", level: 1 })).toBeVisible();
  await expect(
    page.getByText(/Automatic prices are off on this installation: the search is not available/),
  ).toBeVisible();
  const box = page.getByLabel("Search by ISIN, name or symbol");
  await expect(box).toBeDisabled();
  // Even a valid ISIN forced into the box and submitted past the disabled button sends nothing.
  await box.evaluate((input) => input.removeAttribute("disabled"));
  await box.fill(ISIN);
  await page.getByRole("search").evaluate((form) => (form as HTMLFormElement).requestSubmit());

  await page.getByLabel("Name", { exact: true }).fill("Esempio Mondo Locale ETF");
  await page.getByLabel("ISIN (optional)").fill(ISIN);
  await page.getByRole("button", { name: "Create security" }).click();
  await expect(
    page.getByText(
      "“Esempio Mondo Locale ETF” added. Automatic prices are off on this installation.",
    ),
  ).toBeVisible();
  expect(searches).toEqual([]);

  const assets = (await (await page.request.get("/api/v1/assets")).json()) as Asset[];
  const created = assets.find((a) => a.isin === ISIN);
  expect(created).toMatchObject({ name: "Esempio Mondo Locale ETF", quote_currency: "EUR" });
  const next = `account=${account.id}&asset=${created!.id}`;
  await expect(page.getByRole("link", { name: "Add holdings I already own" })).toHaveAttribute(
    "href",
    `/holdings/existing?${next}`,
  );
  await page.getByRole("link", { name: "Add a transaction" }).click();
  await expect(page.getByRole("heading", { name: "New transaction", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Security", { exact: true })).toHaveValue(created!.id);
  await expect(page.getByLabel("Account", { exact: true })).toHaveValue(account.id);

  // The same ISIN again: the page links to the security the user already has.
  await page.goto(`/holdings/add?account=${account.id}`);
  await page.getByLabel("Name", { exact: true }).fill("Esempio Doppio");
  await page.getByLabel("ISIN (optional)").fill(ISIN);
  await page.getByRole("button", { name: "Create security" }).click();
  await expect(
    page.getByText("You already have this security: “Esempio Mondo Locale ETF”."),
  ).toBeVisible();
  await page.getByRole("link", { name: "Open" }).click();
  await expect(page).toHaveURL(`/holdings/${created!.id}?account=${account.id}`);
});

type Summary = { cash: string };
type Page = { total: number };
type Position = { name: string; quantity: string; cost_basis: string };

async function prepare(page: import("@playwright/test").Page, prefix: string) {
  await logIn(page, await newUser(prefix));
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  await apiPost(page, "/transactions", {
    account_id: account.id,
    type: "DEPOSIT",
    trade_date: "2025-01-02",
    total_amount: "1000.00",
  });
  const asset = (name: string, asset_type: string, quote_currency: string, quote_unit: string) =>
    apiPost<Created>(page, "/assets", { name, asset_type, quote_currency, quote_unit });
  return {
    account,
    etf: await asset("Esempio Mondo ETF", "ETF", "EUR", "CURRENCY"),
    btp: await asset("Esempio BTP 2031", "BOND", "EUR", "PERCENT_OF_NOMINAL"),
    usd: await asset("Example Robotics Inc.", "STOCK", "USD", "CURRENCY"),
  };
}

const getJson = async <T>(page: import("@playwright/test").Page, path: string) =>
  (await (await page.request.get(`/api/v1${path}`)).json()) as T;

test("add holdings I already own: an ETF, a bond and a dollar stock; the cash stays", async ({
  page,
}) => {
  const { account, etf, btp, usd } = await prepare(page, "existing");
  await page.goto(`/holdings/existing?account=${account.id}&asset=${etf.id}`);
  await expect(page.getByRole("heading", { name: "Add holdings I already own" })).toBeVisible();
  await expect(page.getByLabel("Account", { exact: true })).toHaveValue(account.id);
  await expect(page.getByLabel("Security, row 1")).toHaveValue(etf.id);
  await page.getByLabel("Held on").fill("2025-12-31");

  await page.getByLabel("Quantity, row 1").fill("19");
  await page.getByLabel("Average cost, row 1").fill("102");
  await page.getByLabel("Security, row 2").selectOption(btp.id);
  await page.getByLabel("Nominal (EUR), row 2").fill("5,000");
  await page.getByLabel("Average cost, row 2").fill("98.5");
  await page.getByLabel("Security, row 3").selectOption(usd.id);
  await page.getByLabel("Quantity, row 3").fill("30");
  await page.getByLabel("Average cost, row 3").fill("94.10");
  await page.getByLabel("Total cost (EUR), row 3").fill("2,595.00");
  const rows = page.locator("table.add-rows tbody tr");
  await expect(rows.nth(0)).toContainText("€1,938.00");
  await expect(rows.nth(1)).toContainText("€4,925.00");
  await expect(page.getByText(/^3 securities ready/)).toBeVisible();

  await page.getByRole("button", { name: "Save 3 securities" }).click();
  await expect(page).toHaveURL(`/holdings?account=${account.id}`);
  await expect(page.getByText("3 securities added to “Conto”.")).toBeVisible();
  await expect(page.getByRole("row", { name: /Esempio BTP 2031/ })).toBeVisible();

  expect((await getJson<Summary>(page, `/accounts/${account.id}/summary`)).cash).toBe("1000.00");
  expect((await getJson<Page>(page, `/transactions?account_id=${account.id}`)).total).toBe(4);
  const positions = await getJson<Position[]>(page, `/accounts/${account.id}/positions`);
  expect(positions.map((p) => [p.name, p.quantity, p.cost_basis]).sort()).toEqual([
    ["Esempio BTP 2031", "5000", "4925.0"],
    ["Esempio Mondo ETF", "19", "1938"],
    ["Example Robotics Inc.", "30", "2595.00"],
  ]);
});

test("add holdings I already own: a row the server refuses, and nothing is stored", async ({
  page,
}) => {
  const { account, etf } = await prepare(page, "existing-refused");
  await page.goto(`/holdings/existing?account=${account.id}`);
  await page.getByLabel("Security, row 1").selectOption(etf.id);
  await page.getByLabel("Quantity, row 1").fill("10");
  await page.getByLabel("Average cost, row 1").fill("100");
  // Too large a cost to store: the server's dry run refuses the row (D-98).
  await page.getByLabel("Security, row 2").selectOption(etf.id);
  await page.getByLabel("Quantity, row 2").fill("999999999999999");
  await page.getByLabel("Average cost, row 2").fill("999999999999999");
  await expect(page.getByLabel("Security, row 2")).toHaveAccessibleDescription("Also in row 1.");
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST") posts.push(new URL(r.url()).pathname);
  });
  await page.getByRole("button", { name: "Save 2 securities" }).click();
  // What only Save does: a fresh dry run, refused, then no batch; the row is named and focused.
  await expect(page.getByRole("alert").filter({ hasText: "Check row 2." })).toBeVisible();
  await expect(page.getByLabel("Average cost, row 2")).toBeFocused();
  await expect(page.getByLabel("Average cost, row 2")).toHaveAccessibleDescription(/too many digits/i);
  expect(posts).toEqual(["/api/v1/transactions/batch/preview"]);
  await expect(page).toHaveURL(/\/holdings\/existing/);
  expect((await getJson<Page>(page, `/transactions?account_id=${account.id}`)).total).toBe(1);
});

test("the entry points: holdings, the conflict dialog and a new account", async ({ page }) => {
  await logIn(page, await newUser("entry"));
  // A new account: its notice links to screen 10 with it chosen.
  await page.goto("/accounts");
  await page.getByRole("button", { name: "Create the first account" }).click();
  await page.getByRole("dialog").getByLabel("Name").fill("Conto");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Account “Conto” created.")).toBeVisible();
  const accounts = (await (await page.request.get("/api/v1/accounts")).json()) as Created[];
  await page.getByRole("link", { name: "Add holdings I already own" }).click();
  await expect(page).toHaveURL(`/holdings/existing?account=${accounts[0].id}`);
  await expect(page.getByLabel("Account", { exact: true })).toHaveValue(accounts[0].id);

  // The holdings page, empty: both ways in, from its head and from the empty state.
  await page.goto("/holdings");
  await expect(page.getByText("No securities in this view")).toBeVisible();
  await expect(page.getByRole("link", { name: "Add holdings I already own" })).toHaveCount(2);
  await page.getByRole("link", { name: "Add a security" }).first().click();
  await expect(page.getByRole("heading", { name: "Add a security", level: 1 })).toBeVisible();

  // A sale of what is not held: the conflict dialog leads to screen 10 for an earlier date.
  const asset = await apiPost<Created>(page, "/assets", {
    name: "Esempio Industrie SpA",
    asset_type: "STOCK",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
  });
  await page.goto("/transactions/new");
  await page.getByRole("radio", { name: "Sell" }).check();
  await page.getByLabel("Security", { exact: true }).selectOption(asset.id);
  await page.getByLabel("Date", { exact: true }).fill("2026-03-02");
  await page.getByLabel("Quantity").fill("10");
  await page.getByLabel("Unit price (EUR)").fill("12");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "This change cannot be saved" });
  await expect(dialog).toContainText("Choose a date before Mar 2, 2026.");
  await expect(dialog).toContainText("What you typed in this form will be lost.");
  await dialog.getByRole("link", { name: "Add holdings I already own" }).click();
  await expect(page).toHaveURL(
    `/holdings/existing?account=${accounts[0].id}&asset=${asset.id}&before=2026-03-02`,
  );
  await expect(page.getByText("To cover the sale that is in conflict, choose a date before Mar 2, 2026.")).toBeVisible();
  await expect(page.getByLabel("Security, row 1")).toHaveValue(asset.id);
  await page.getByLabel("Held on").fill("2026-01-02");
  await page.getByLabel("Quantity, row 1").fill("10");
  await page.getByLabel("Average cost, row 1").fill("10");
  await page.getByRole("button", { name: "Save 1 security" }).click();
  await expect(page.getByText("1 security added to “Conto”.")).toBeVisible();
  await expect(page.getByRole("row", { name: /Esempio Industrie SpA/ })).toContainText("10");
});
