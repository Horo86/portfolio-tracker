import { expect, type Page, test } from "@playwright/test";

import { apiPost, logIn, newUser } from "./helpers";

// Invented accounts, securities and amounts only.
type Created = { id: string };

async function prepare(page: Page) {
  const eur = await apiPost<Created>(page, "/accounts", { name: "Conto titoli", currency: "EUR" });
  const world = await apiPost<Created>(page, "/assets", {
    name: "Esempio Mondo ETF",
    asset_type: "ETF",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
  });
  return { eur, world };
}

test.beforeEach(async ({ page }) => {
  await logIn(page, await newUser("transactions"));
});

test("the list pages by 50, filters through the URL and survives a reload", async ({ page }) => {
  const { eur, world } = await prepare(page);
  for (let day = 1; day <= 55; day++) {
    await apiPost(page, "/transactions", {
      account_id: eur.id,
      type: "DEPOSIT",
      trade_date: `2026-03-${String((day % 28) + 1).padStart(2, "0")}`,
      total_amount: `${day}.00`,
    });
  }
  await apiPost(page, "/transactions", {
    account_id: eur.id,
    type: "BUY",
    trade_date: "2026-04-01",
    asset_id: world.id,
    quantity: "15",
    unit_price: "101.80",
    gross_amount: "1527.00",
    fees: "3.00",
    total_amount: "1530.00",
  });

  await page.getByRole("link", { name: "Transactions" }).click();
  await expect(page.getByRole("heading", { name: "Transactions", level: 1 })).toBeVisible();
  await expect(page.getByText("View: all accounts (1) · 56 transactions")).toBeVisible();
  await expect(page.getByText("Rows 1–50 of 56", { exact: true })).toBeVisible();
  const firstRow = page.getByRole("row").nth(1);
  await expect(firstRow).toContainText("Esempio Mondo ETF");
  await expect(firstRow).toContainText("-€1,530.00");

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Rows 51–56 of 56", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/[?&]page=2/);
  await page.reload();
  await expect(page.getByText("Rows 51–56 of 56", { exact: true })).toBeVisible();

  await page.getByLabel("Type").selectOption({ label: "Buy" });
  await expect(page.getByText("Rows 1–1 of 1", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\?type=BUY$/);
  await page.getByLabel("From").fill("2026-04-02");
  await expect(page.getByText("No transaction matches the filters.")).toBeVisible();
  await page.goBack();
  await expect(page.getByText("Rows 1–1 of 1", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.getByText("Rows 1–50 of 56", { exact: true })).toBeVisible();
});
