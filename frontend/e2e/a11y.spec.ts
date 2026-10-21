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

    // The form with its summary filled in, and the conflict dialog; nothing is saved.
    await page.getByRole("link", { name: "Aggiungi transazione" }).click();
    await page.getByRole("radio", { name: "Vendita" }).check();
    const account = page.getByLabel("Conto", { exact: true });
    await account.selectOption({ label: "Conto titoli (EUR)" });
    await page.getByLabel("Titolo").selectOption({ label: "Esempio Azionario Mondo ETF · EUR" });
    await page.getByLabel("Quantità").fill("15");
    await page.getByLabel("Prezzo unitario (EUR)").fill("101,80");
    const summary = page.getByRole("complementary", { name: "Riepilogo" });
    await expect(summary).toContainText("Quantità dopo35");
    await expectAccessible(page, `transaction form, ${theme}`);
    await page.getByLabel("Quantità").fill("500");
    await expect(summary).toContainText("Quantità insufficiente");
    await expectAccessible(page, `transaction form with a conflict, ${theme}`);
    await page.getByRole("button", { name: "Salva", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expectAccessible(page, `conflict dialog, ${theme}`);
    await page.keyboard.press("Escape");

    // The edit page of a coupon (income fields) and its delete dialog; nothing is saved.
    await page.goto("/transactions");
    await page.getByRole("link", { name: /^Modifica: Cedola/ }).click();
    await expect(page.getByLabel("Imposte trattenute (EUR)")).toHaveValue("9,38");
    await expect(page.getByRole("complementary", { name: "Riepilogo" })).toContainText(
      "Netto incassato",
    );
    await expectAccessible(page, `edit page, ${theme}`);
    await page.getByRole("button", { name: "Elimina…" }).click();
    await expect(page.getByRole("dialog")).toContainText("Dopo l'eliminazione:");
    await expectAccessible(page, `delete dialog, ${theme}`);
    await page.keyboard.press("Escape");

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
