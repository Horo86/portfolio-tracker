import { expect, test } from "@playwright/test";

import { apiPatch, DEMO, expectAccessible, logIn } from "./helpers";

// axe (WCAG 2.1 A and AA) on the screens of this slice, in each of the four themes: their
// colors differ, so contrast is checked in all of them. The demo user of the development
// data has real-looking (invented) accounts and figures, in Italian.
const THEMES = ["sobrio", "moderno", "ambra", "grafite"] as const;

for (const theme of THEMES) {
  test(`no axe violation in the ${theme} theme`, async ({ page }) => {
    await page.addInitScript((name) => window.localStorage.setItem("pt.theme", name), theme);
    await page.goto("/login");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expectAccessible(page, `login, ${theme}`);

    await logIn(page, DEMO);
    await apiPatch(page, "/me", { theme });
    await page.goto("/accounts");
    await expect(page.getByRole("region", { name: "Conto estero" })).toContainText("$");
    await expectAccessible(page, `accounts, ${theme}`);

    await page.getByRole("button", { name: "Nuovo conto" }).click();
    await expectAccessible(page, `new account dialog, ${theme}`);
    await page.keyboard.press("Escape");
    await page
      .getByRole("region", { name: "Conto titoli" })
      .getByRole("button", { name: "Modifica" })
      .click();
    await expectAccessible(page, `edit account dialog, ${theme}`);
    await page.getByRole("button", { name: "Elimina conto…" }).click();
    await expectAccessible(page, `delete account dialog, ${theme}`);
    await page.keyboard.press("Escape");

    await page.goto("/transactions");
    await expect(page.getByText("Righe 1–10 di 10", { exact: true })).toBeVisible();
    await expectAccessible(page, `transactions, ${theme}`);

    await page.goto("/settings");
    await expect(page.getByRole("list", { name: "Sessioni attive" })).toBeVisible();
    await expectAccessible(page, `settings, ${theme}`);
    await page.getByRole("button", { name: "Cambia password" }).click();
    await expectAccessible(page, `password dialog, ${theme}`);
    await page.keyboard.press("Escape");

    await page.goto("/about");
    await expectAccessible(page, `about, ${theme}`);
    await page.goto("/no/such/page");
    await expectAccessible(page, `not found, ${theme}`);
  });
}

test.afterAll(async ({ browser }) => {
  // Leave the demo user as the development data made it.
  const page = await browser.newPage();
  await logIn(page, DEMO);
  await apiPatch(page, "/me", { theme: "moderno" });
  await page.close();
});
