import { expect, test } from "@playwright/test";

import { EMPTY } from "../playwright.config";
import { DASHBOARD, expectAccessible } from "./helpers";

test.use({ baseURL: EMPTY });

// The smoke test (Q-8): a fresh installation from the first screen to the logout. It runs
// once per server start: afterwards the instance has its administrator.
test("first run: administrator, first account, theme and language, logout", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Restore from a backup" })).toBeDisabled();
  await expectAccessible(page, "first run");

  await page.getByRole("button", { name: "Start from scratch" }).click();
  await page.getByLabel("Username").fill("owner");
  await page.getByLabel("Password", { exact: true }).fill("first-run password");
  await page.getByLabel("Repeat the password").fill("first-run password");
  await expectAccessible(page, "create the administrator");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("radio", { name: "English" })).toBeChecked();
  await page.getByRole("button", { name: "Create the administrator" }).click();
  await expect(page.getByRole("heading", { name: DASHBOARD, level: 1 })).toBeVisible();

  await page.getByRole("link", { name: "Accounts" }).click();
  await page.getByRole("button", { name: "Create the first account" }).click();
  const dialog = page.getByRole("dialog", { name: "New account" });
  await dialog.getByLabel("Name").fill("Conto di prova");
  await dialog.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("region", { name: "Conto di prova" })).toBeVisible();

  await page.getByRole("link", { name: "Settings" }).click();
  await page.locator("label", { hasText: "Graphite" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "grafite");
  await page.getByLabel("Language").selectOption("it");
  await expect(page.getByRole("heading", { name: "Impostazioni", level: 1 })).toBeVisible();
  await expect(page.getByText("Impostazioni salvate.")).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "Impostazioni", level: 1 })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "grafite");
  await expect(page.locator("html")).toHaveAttribute("lang", "it");

  await page.getByRole("button", { name: "Esci", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Accedi" })).toBeVisible();
  // Before login the browser keeps the last user's language and theme (Q-7).
  await expect(page.locator("html")).toHaveAttribute("data-theme", "grafite");
});
