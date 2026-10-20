import { expect, type Page, test } from "@playwright/test";

import { apiPost, logIn, newUser } from "./helpers";

type Account = { id: string; name: string };

const createAccount = (page: Page, name: string, currency = "EUR") =>
  apiPost<Account>(page, "/accounts", { name, currency });

/** Where the keyboard focus is: inside the open dialog, outside the page (the browser's own
 * controls, which jsdom cannot show), or on the page behind it. */
const focusPlace = (page: Page) =>
  page.evaluate(() => {
    const active = document.activeElement;
    if (!active || active === document.body) return "browser";
    return document.querySelector("dialog[open]")?.contains(active) ? "dialog" : "page";
  });

test.beforeEach(async ({ page }) => {
  await logIn(page, await newUser("accounts"));
});

test("a dialog keeps the focus inside, closes with Escape and gives it back", async ({ page }) => {
  await createAccount(page, "Conto titoli");
  await page.goto("/accounts");
  const newButton = page.getByRole("button", { name: "New account" });
  await newButton.click();
  const dialog = page.getByRole("dialog", { name: "New account" });
  await expect(dialog.getByLabel("Name")).toBeFocused();

  // Twice around: name, currency, broker, cancel, create, then back to the name.
  for (let step = 0; step < 12; step++) {
    await page.keyboard.press("Tab");
    expect(await focusPlace(page), `after ${step + 1} tabs`).not.toBe("page");
  }
  await page.keyboard.press("Shift+Tab");
  expect(await focusPlace(page)).not.toBe("page");

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(newButton).toBeFocused();

  // The same for a card's Edit button, closed with Cancel.
  const edit = page.getByRole("region", { name: "Conto titoli" }).getByRole("button", {
    name: "Edit",
  });
  await edit.click();
  await expect(page.getByRole("dialog", { name: "Edit account" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(edit).toBeFocused();
  await edit.press("Enter");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(edit).toBeFocused();
});

test("an account is deleted only after its exact name is typed", async ({ page }) => {
  const account = await createAccount(page, "Conto da chiudere");
  await apiPost(page, "/transactions", {
    account_id: account.id,
    type: "DEPOSIT",
    trade_date: "2026-01-05",
    total_amount: "100.00",
  });
  await page.goto("/accounts");
  await page
    .getByRole("region", { name: "Conto da chiudere" })
    .getByRole("button", { name: "Edit" })
    .click();
  await page.getByRole("button", { name: "Delete account…" }).click();

  const dialog = page.getByRole("dialog", { name: "Delete the account “Conto da chiudere”?" });
  await expect(dialog).toContainText("Its 1 transaction will be deleted too.");
  const confirm = dialog.getByLabel("Type the account name to confirm");
  const submit = dialog.getByRole("button", { name: "Delete account and transactions" });
  await expect(confirm).toBeFocused();
  await expect(submit).toBeDisabled();
  await confirm.fill("Conto da");
  await expect(submit).toBeDisabled();
  await confirm.fill("conto da chiudere");
  await expect(submit).toBeDisabled();
  await confirm.fill("Conto da chiudere");
  await expect(submit).toBeEnabled();
  await submit.click();

  // The live region takes the focus; the message is an element inside it.
  const notice = page
    .getByRole("status")
    .filter({ hasText: "Account “Conto da chiudere” deleted with its 1 transaction." });
  await expect(notice).toBeFocused();
  await expect(page.getByRole("region", { name: "Conto da chiudere" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: "Create the first account" })).toBeVisible();
});

test("the account in view stays in ?account= across pages, reload and back", async ({ page }) => {
  await createAccount(page, "Conto titoli");
  const foreign = await createAccount(page, "Conto estero", "USD");
  await page.goto("/accounts");
  await page.getByRole("region", { name: "Conto estero" }).getByRole("link", { name: "Open" }).click();

  const inView = page.getByLabel("Account in view");
  await expect(page).toHaveURL(`/?account=${foreign.id}`);
  await expect(inView).toHaveValue(foreign.id);

  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(`/settings?account=${foreign.id}`);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  await expect(inView).toHaveValue(foreign.id);
  await expect(page.getByText("Values in USD")).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(`/?account=${foreign.id}`);
  await inView.selectOption({ label: "All accounts" });
  await expect(page).toHaveURL("/");
  await expect(page.getByText("2 accounts · values in EUR")).toBeVisible();
});
