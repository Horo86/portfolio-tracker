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

test("buy, then withdrawal, then buy again: the summary never mixes the two", async ({ page }) => {
  const { eur } = await prepare(page);
  await apiPost(page, "/transactions", {
    account_id: eur.id,
    type: "DEPOSIT",
    trade_date: "2026-01-05",
    total_amount: "5000.00",
  });
  await page.goto("/transactions/new");
  const summary = page.getByRole("complementary", { name: "Summary" });
  await page.getByLabel("Security").selectOption({ label: "Esempio Mondo ETF · EUR" });
  await page.getByLabel("Date", { exact: true }).fill("2026-03-02");
  await page.getByLabel("Quantity").fill("15");
  await page.getByLabel("Unit price (EUR)").fill("101.80");
  await expect(summary).toContainText("Total cost€1,527.00");

  await page.getByRole("radio", { name: "Withdrawal" }).check();
  await page.getByLabel("Amount (EUR)").fill("1000");
  await expect(summary).toContainText("Amount€1,000.00");
  await expect(summary).toContainText("Cash after€4,000.00");

  // The withdrawal's answer has no gross and no position: it is not read as a buy's.
  await page.getByRole("radio", { name: "Buy" }).check();
  await expect(summary).toContainText("Gross amount (15 × 101.80)€1,527.00");
  await expect(summary).toContainText("Total cost€1,527.00");
  await expect(summary).toContainText("Quantity after15");
  await expect(page.getByRole("heading", { name: "New transaction", level: 1 })).toBeVisible();
  await expect(page.getByText("Something went wrong")).toHaveCount(0);
});

test("edit a buy from the filtered list, a refused edit, and delete a sale", async ({ page }) => {
  const { eur, world } = await prepare(page);
  const trades = [
    { type: "DEPOSIT", trade_date: "2026-01-05", total_amount: "5000.00" },
    {
      type: "BUY", trade_date: "2026-03-02", asset_id: world.id, quantity: "15",
      unit_price: "101.80", gross_amount: "1527.00", fees: "3.00", total_amount: "1530.00",
    },
    {
      type: "SELL", trade_date: "2026-06-10", asset_id: world.id, quantity: "5",
      unit_price: "110", gross_amount: "550.00", total_amount: "550.00",
    },
  ];
  for (const tx of trades) await apiPost(page, "/transactions", { account_id: eur.id, ...tx });

  await page.goto("/transactions?type=BUY");
  await page.getByRole("link", { name: "Edit: Buy, Esempio Mondo ETF, Mar 2, 2026" }).click();
  await expect(page.getByRole("heading", { name: "Edit transaction", level: 1 })).toBeFocused();
  await expect(page.getByText("Buy of 15 Esempio Mondo ETF · Conto titoli")).toBeVisible();
  await expect(page.getByLabel("Fees (EUR)")).toHaveValue("3.00");

  // A new quantity: the gross is computed again; the sale that follows still fits.
  const summary = page.getByRole("complementary", { name: "Summary" });
  await page.getByLabel("Quantity").fill("20");
  await expect(summary).toContainText("Gross amount (20 × 101.80)€2,036.00");
  await expect(summary).toContainText("Total cost€2,039.00");
  await expect(summary).toContainText("Change in cash-€509.00");
  await expect(summary).toContainText("Quantity after15");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Changes saved." })).toBeVisible();
  await expect(page).toHaveURL("/transactions?type=BUY");
  await expect(page.getByRole("row").nth(1)).toContainText("-€2,039.00");

  // Fewer than the later sale needs: shown live, refused on Save, nothing stored.
  await page.getByRole("link", { name: "Edit: Buy, Esempio Mondo ETF, Mar 2, 2026" }).click();
  await page.getByLabel("Quantity").fill("4");
  await expect(summary).toContainText(
    "A later transaction of this security would be left without enough quantity: " +
      "4 held before it, -1 after.",
  );
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const refused = page.getByRole("dialog", { name: "This change cannot be saved" });
  await expect(refused).toContainText(
    "First transaction in conflict: Sell of 5 on Jun 10, 2026 (you hold 4).",
  );
  await refused.getByRole("button", { name: "Back to the form" }).click();
  await page.getByRole("link", { name: "Cancel" }).click();
  await expect(page.getByRole("row").nth(1)).toContainText("-€2,039.00");

  // Delete the sale: the dialog says what follows, from the server's dry run.
  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("link", { name: "Edit: Sell, Esempio Mondo ETF, Jun 10, 2026" }).click();
  await page.getByRole("button", { name: "Delete…" }).click();
  const remove = page.getByRole("dialog", { name: "Delete this transaction?" });
  await expect(remove).toContainText("Sell of 5 Esempio Mondo ETF on Jun 10, 2026 · Conto titoli.");
  await expect(remove).toContainText("quantity of Esempio Mondo ETF: 20");
  await expect(remove).toContainText("cash of Conto titoli: €2,961.00");
  await remove.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Transaction deleted." })).toBeVisible();
  await expect(page.getByText("View: all accounts (1) · 2 transactions")).toBeVisible();
});

test("a dividend and a split through the form", async ({ page }) => {
  const { eur, world } = await prepare(page);
  await apiPost(page, "/transactions", {
    account_id: eur.id, type: "BUY", trade_date: "2026-02-02", asset_id: world.id,
    quantity: "10", unit_price: "100", gross_amount: "1000.00", total_amount: "1000.00",
    affects_cash: false,
  });
  await page.goto("/transactions/new");
  const summary = page.getByRole("complementary", { name: "Summary" });

  await page.getByRole("radio", { name: "Dividend" }).check();
  await page.getByLabel("Security").selectOption({ label: "Esempio Mondo ETF · EUR" });
  await page.getByLabel("Date", { exact: true }).fill("2026-05-20");
  await page.getByLabel("Gross amount (EUR)").fill("12.50");
  await page.getByLabel("Taxes withheld (EUR)").fill("3.25");
  await expect(summary).toContainText("Net amount received€9.25");
  await expect(summary).toContainText("Cash movement+€9.25");
  await page.getByRole("button", { name: "Save and add another" }).click();
  await expect(page.getByText("Transaction saved.")).toBeVisible();

  await page.getByRole("radio", { name: "Split" }).check();
  await page.getByLabel("Date", { exact: true }).fill("2026-07-01");
  await page.getByLabel("New", { exact: true }).fill("2");
  await page.getByLabel("Old", { exact: true }).fill("1");
  await expect(summary).toContainText("Quantity after20");
  await expect(summary).toContainText("New average cost€50.00");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Transaction saved." })).toBeVisible();
  const split = page.getByRole("row").nth(1);
  await expect(split).toContainText("Split");
  await expect(split).toContainText("2:1");
  await expect(page.getByRole("row").nth(2)).toContainText("+€9.25");
});

test("a security created by hand from the form, then bought", async ({ page }) => {
  await apiPost(page, "/accounts", { name: "Conto titoli", currency: "EUR" });
  await page.goto("/transactions/new");
  await expect(page.getByLabel("Security")).toHaveAccessibleDescription("No securities yet.");
  await page.getByRole("button", { name: "New security…" }).click();
  const dialog = page.getByRole("dialog", { name: "New security" });
  await dialog.getByLabel("Name").fill("Esempio Titolo di Stato 2033");
  await dialog.getByLabel("Type").selectOption({ label: "Bond" });
  await expect(dialog.getByLabel("The price is expressed in")).toHaveValue("PERCENT_OF_NOMINAL");
  await dialog.getByLabel("ISIN (optional)").fill("ZZ0000002012");
  await dialog.getByRole("button", { name: "Create security" }).click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByText("Security “Esempio Titolo di Stato 2033” created and chosen."),
  ).toBeVisible();
  const security = page.getByLabel("Security");
  await expect(security).toHaveAccessibleDescription(
    "Automatic prices for this ISIN are being looked up.",
  );

  await page.getByLabel("Nominal (EUR)").fill("5000");
  await page.getByLabel("Price (% of nominal)").fill("98.50");
  const summary = page.getByRole("complementary", { name: "Summary" });
  await expect(summary).toContainText("Gross amount (5,000 × 98.50%)€4,925.00");
  await expect(summary).toContainText("New average cost98.50%");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Transaction saved." })).toBeVisible();
  await expect(page.getByRole("row").nth(1)).toContainText("Esempio Titolo di Stato 2033");

  // The same ISIN again is refused at its field.
  await page.getByRole("link", { name: "Add transaction" }).click();
  await page.getByRole("button", { name: "New security…" }).click();
  await dialog.getByLabel("Name").fill("Doppione");
  await dialog.getByLabel("ISIN (optional)").fill("ZZ0000002012");
  await dialog.getByRole("button", { name: "Create security" }).click();
  await expect(dialog.getByLabel("ISIN (optional)")).toHaveAccessibleDescription(
    /You already have a security with this ISIN\./,
  );
});

test("bulk edit and bulk delete with a refusal", async ({ page }) => {
  const { eur, world } = await prepare(page);
  await apiPost(page, "/accounts", { name: "Conto deposito", currency: "EUR" });
  const stock = await apiPost<Created>(page, "/assets", {
    name: "Esempio Industrie SpA",
    asset_type: "STOCK",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
  });
  const trades = [
    { type: "DEPOSIT", trade_date: "2026-01-05", total_amount: "5000.00" },
    {
      type: "BUY", trade_date: "2026-02-02", asset_id: world.id, quantity: "10",
      unit_price: "100", gross_amount: "1000.00", total_amount: "1000.00",
    },
    {
      type: "BUY", trade_date: "2026-03-03", asset_id: stock.id, quantity: "20",
      unit_price: "10", gross_amount: "200.00", total_amount: "200.00",
    },
    {
      type: "SELL", trade_date: "2026-05-04", asset_id: world.id, quantity: "4",
      unit_price: "110", gross_amount: "440.00", total_amount: "440.00",
    },
    {
      type: "DIVIDEND", trade_date: "2026-06-01", asset_id: stock.id,
      gross_amount: "10.00", taxes: "2.60", total_amount: "7.40",
    },
  ];  // prettier-ignore
  for (const tx of trades) await apiPost(page, "/transactions", { account_id: eur.id, ...tx });
  const select = (name: string) => page.getByRole("checkbox", { name }).check();
  const buyWorld = "Select: Buy, Esempio Mondo ETF, Feb 2, 2026";
  const sellWorld = "Select: Sell, Esempio Mondo ETF, May 4, 2026";

  // The buy and the sale move together to the other account, without moving cash.
  await page.goto("/transactions");
  await select(buyWorld);
  await select(sellWorld);
  await expect(page.getByText("2 selected")).toBeVisible();
  await page.getByRole("button", { name: "Edit selected" }).click();
  const edit = page.getByRole("dialog", { name: "Edit 2 transactions together" });
  await edit.getByLabel("Account").selectOption({ label: "Conto deposito (EUR)" });
  await edit.getByLabel("Do not move cash").selectOption({ label: "Yes" });
  await edit.getByRole("button", { name: "Apply to 2 transactions" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Changes saved for 2 transactions." }),
  ).toBeVisible();
  await page.getByLabel("Account in view").selectOption({ label: "Conto deposito" });
  await expect(page.getByText("View: Conto deposito · 2 transactions")).toBeVisible();
  await expect(page.getByRole("row").nth(1)).toContainText("cash not moved");

  // The sale alone back to the first account: it would sell what is not there.
  await select(sellWorld);
  await page.getByRole("button", { name: "Edit selected" }).click();
  const one = page.getByRole("dialog", { name: "Edit 1 transaction" });
  await one.getByLabel("Account").selectOption({ label: "Conto titoli (EUR)" });
  await one.getByRole("button", { name: "Apply to 1 transaction" }).click();
  const refused = page.getByRole("dialog", { name: "This change cannot be saved" });
  await expect(refused).toContainText(
    "First transaction in conflict: Sell of 4 on May 4, 2026 (you hold 0).",
  );
  await refused.getByRole("button", { name: "Back to the form" }).click();
  // The focus is back on the button that was pressed, in the bulk dialog still open.
  await expect(one.getByRole("button", { name: "Apply to 1 transaction" })).toBeFocused();
  await expect(one.getByLabel("Account")).toHaveValue(eur.id);
  await one.getByRole("button", { name: "Cancel" }).click();

  // The buy alone cannot go, the buy and the sale together can.
  await page.getByRole("checkbox", { name: sellWorld }).uncheck();
  await select(buyWorld);
  await page.getByRole("button", { name: "Delete selected" }).click();
  const remove = page.getByRole("dialog", { name: "Delete 1 transaction?" });
  await remove.getByRole("button", { name: "Delete 1 transaction" }).click();
  await expect(refused).toContainText("Sell of 4 on May 4, 2026 (you hold 0).");
  await refused.getByRole("button", { name: "Back to the form" }).click();
  await expect(remove.getByRole("button", { name: "Delete 1 transaction" })).toBeFocused();
  await remove.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("checkbox", { name: "Select every row of this page" }).check();
  await page.getByRole("button", { name: "Delete selected" }).click();
  await page.getByRole("button", { name: "Delete 2 transactions" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "2 transactions deleted." }),
  ).toBeVisible();
  await expect(page.getByText("View: Conto deposito · 0 transactions")).toBeVisible();
});
