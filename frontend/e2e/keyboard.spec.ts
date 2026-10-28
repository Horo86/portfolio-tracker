import { expect, type Locator, type Page, test } from "@playwright/test";

import { apiPost, DASHBOARD, newUser } from "./helpers";

// Keyboard only (M5 slice 6, WCAG 2.1.1): Tab moves between controls, typing, arrows, Space
// and Enter do the rest. No click, no fill. Invented accounts and securities only.

type Created = { id: string };

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

test("login with the keyboard only", async ({ page }) => {
  const user = await newUser("keys");
  await page.goto("/login");
  await typeInto(page, page.getByLabel("Username"), user.username);
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password")).toBeFocused();
  await page.keyboard.type(user.password);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: DASHBOARD, level: 1 })).toBeVisible();
});

test("a transaction entered end to end with the keyboard only", async ({ page }) => {
  const user = await newUser("keys");
  await page.goto("/login");
  await typeInto(page, page.getByLabel("Username"), user.username);
  await typeInto(page, page.getByLabel("Password"), user.password);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: DASHBOARD, level: 1 })).toBeVisible();
  // Prepared through the API, then the page is loaded again (F5 for a keyboard user).
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto tastiera", currency: "EUR" });
  await apiPost(page, "/assets", {
    name: "Esempio Tastiera ETF", asset_type: "ETF", quote_currency: "EUR", quote_unit: "CURRENCY",
  }); // prettier-ignore
  await apiPost(page, "/transactions", {
    account_id: account.id, type: "DEPOSIT", trade_date: "2026-01-05", total_amount: "5000.00",
  }); // prettier-ignore
  await page.reload();

  const menu = page.getByRole("navigation", { name: "Main menu" });
  await tabTo(page, menu.getByRole("link", { name: "Transactions" }));
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Transactions", level: 1 })).toBeVisible();
  await tabTo(page, page.getByRole("link", { name: "Add transaction" }));
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "New transaction", level: 1 })).toBeFocused();

  // The type: arrows move within the radio group (Buy is chosen first).
  await tabTo(page, page.getByRole("radio", { name: "Buy" }));
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("radio", { name: "Sell" })).toBeChecked();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("radio", { name: "Buy" })).toBeChecked();

  const account_ = page.getByLabel("Account", { exact: true });
  await tabTo(page, account_);
  await page.keyboard.type("Conto tastiera");
  await expect(account_).toHaveValue(account.id);
  const security = page.getByLabel("Security", { exact: true });
  await tabTo(page, security);
  await page.keyboard.type("Esempio Tastiera");
  await expect(security).not.toHaveValue("");

  await typeInto(page, page.getByLabel("Date", { exact: true }), "02/03/2026");
  await tabTo(page, page.getByLabel("Time (optional)"));
  await page.keyboard.type("1030");
  await typeInto(page, page.getByLabel("Value date (optional)", { exact: true }), "04/03/2026");
  await typeInto(page, page.getByLabel("Quantity"), "10");
  await typeInto(page, page.getByLabel("Unit price (EUR)"), "100");
  await typeInto(page, page.getByLabel("Fees (EUR)"), "2");
  await typeInto(page, page.getByLabel("Notes (optional)"), "entered with the keyboard");
  const noCash = page.getByLabel("Do not move cash");
  await tabTo(page, noCash);
  await page.keyboard.press("Space");
  await expect(noCash).toBeChecked();
  await page.keyboard.press("Space"); // back: this purchase moves cash
  await expect(noCash).not.toBeChecked();

  await tabTo(page, page.getByRole("button", { name: "Save", exact: true }));
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status").filter({ hasText: "Transaction saved." })).toBeVisible();
  await expect(page).toHaveURL("/transactions");
  const row = page.getByRole("row", { name: /Esempio Tastiera ETF/ });
  await expect(row).toContainText("Buy");
  await expect(row).toContainText("Mar 2, 2026");
  await expect(row).toContainText("-€1,002.00");
});
