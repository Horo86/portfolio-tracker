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

test("a buy and a sell through the form; an oversell is refused with the conflict dialog", async ({
  page,
}) => {
  const { eur } = await prepare(page);
  await apiPost(page, "/transactions", {
    account_id: eur.id,
    type: "DEPOSIT",
    trade_date: "2026-01-05",
    total_amount: "5000.00",
  });
  await page.goto("/transactions");
  await page.getByRole("link", { name: "Add transaction" }).click();
  await expect(page.getByRole("heading", { name: "New transaction", level: 1 })).toBeFocused();

  const summary = page.getByRole("complementary", { name: "Summary" });
  await page.getByLabel("Security").selectOption({ label: "Esempio Mondo ETF · EUR" });
  await page.getByLabel("Date", { exact: true }).fill("2026-03-02");
  await page.getByLabel("Quantity").fill("15");
  await page.getByLabel("Unit price (EUR)").fill("101.80");
  await page.getByLabel("Fees (EUR)").fill("3");
  await expect(summary).toContainText("Gross amount (15 × 101.80)€1,527.00");
  await expect(summary).toContainText("Total cost€1,530.00");
  await expect(summary).toContainText("Cash movement-€1,530.00");
  await expect(summary).toContainText("Cash after€3,470.00");
  await expect(summary).toContainText("New average cost€102.00");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect(page.getByRole("status").filter({ hasText: "Transaction saved." })).toBeVisible();
  await expect(page).toHaveURL("/transactions");
  const buy = page.getByRole("row").nth(1);
  await expect(buy).toContainText("Buy");
  await expect(buy).toContainText("-€1,530.00");

  await page.getByRole("link", { name: "Add transaction" }).click();
  await page.getByRole("radio", { name: "Sell" }).check();
  await page.getByLabel("Security").selectOption({ label: "Esempio Mondo ETF · EUR" });
  await page.getByLabel("Date", { exact: true }).fill("2026-06-10");
  await page.getByLabel("Quantity").fill("5");
  await page.getByLabel("Unit price (EUR)").fill("110");
  await expect(summary).toContainText("Net amount credited€550.00");
  await expect(summary).toContainText("Quantity after10");
  await expect(summary).toContainText("Average cost€102.00");
  await page.getByRole("button", { name: "Save and add another" }).click();
  await expect(page.getByText("Transaction saved.")).toBeVisible();
  await expect(page.getByLabel("Quantity")).toHaveValue("");

  // Selling more than is held: shown live, refused on Save, nothing stored.
  await page.getByLabel("Quantity").fill("60");
  await page.getByLabel("Unit price (EUR)").fill("110");
  await expect(summary).toContainText(
    "Not enough quantity: you would hold 10 before this transaction, and it would leave -50.",
  );
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "This change cannot be saved" });
  await expect(dialog).toContainText(
    "First transaction in conflict: Sell of 60 on Jun 10, 2026 (you hold 10).",
  );
  await dialog.getByRole("button", { name: "Back to the form" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeFocused();

  await page.getByRole("link", { name: "Cancel" }).click();
  await expect(page.getByText("View: all accounts (1) · 3 transactions")).toBeVisible();
});
