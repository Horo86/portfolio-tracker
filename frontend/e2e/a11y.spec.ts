import { expect, test } from "@playwright/test";

import { apiPatch, DEMO, expectAccessible, logIn } from "./helpers";

// axe (WCAG 2.1 A and AA) on the screens of this slice, in each of the four themes: their
// colors differ, so contrast is checked in all of them. The demo user of the development
// data has real-looking (invented) accounts and figures, in Italian.
const THEMES = ["sobrio", "moderno", "ambra", "grafite"] as const;

for (const theme of THEMES) {
  test(`no axe violation in the ${theme} theme`, async ({ page }) => {
    // One walk through every screen per theme: about 27 s on a quiet machine since screen 12
    // joined it, too close to the default 30 s on a busy runner.
    test.setTimeout(60_000);
    await page.addInitScript((name) => window.localStorage.setItem("pt.theme", name), theme);
    await page.goto("/login");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expectAccessible(page, `login, ${theme}`);

    await logIn(page, DEMO);
    await apiPatch(page, "/me", { theme });
    await page.goto("/");
    await expect(page.getByRole("table", { name: "Per tipo" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Titoli migliori e peggiori" })).toBeVisible();
    await expect(page.locator(".chart-donut svg")).toBeVisible(); // the charts are drawn
    await expectAccessible(page, `dashboard, ${theme}`);
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

    await page.goto("/holdings");
    await expect(page.getByRole("row", { name: /Example Robotics/ })).toBeVisible();
    await expectAccessible(page, `holdings, ${theme}`);
    await page.getByRole("link", { name: "Esempio Titolo di Stato 2031" }).click();
    await expect(page.getByRole("region", { name: "Cedole ricevute" })).toBeVisible();
    await expectAccessible(page, `holding detail, ${theme}`);
    // The edit dialog of a security in use, its locked fields named; nothing is saved.
    await page.getByRole("button", { name: "Modifica titolo" }).click();
    await expect(page.getByRole("dialog", { name: "Modifica titolo" })).toContainText(
      "si possono cambiare solo finché nessuna transazione usa il titolo",
    );
    await expectAccessible(page, `edit security dialog, ${theme}`);
    await page.keyboard.press("Escape");
    // Delete all for a holding, on its detail with one account in view; nothing is saved.
    await page.getByLabel("Conto in vista").selectOption({ label: "Conto titoli" });
    await page.getByRole("button", { name: "Elimina tutte le transazioni del titolo…" }).click();
    await expect(page.getByRole("dialog")).toContainText("Conto titoli · 2 transazioni.");
    await expectAccessible(page, `delete all dialog, ${theme}`);
    await page.keyboard.press("Escape");
    await page.getByLabel("Conto in vista").selectOption({ label: "Tutti i conti" });
    // Screen 12 with the error of a future day, and its delete dialog; nothing is saved.
    await page.getByRole("link", { name: "Prezzi a mano", exact: true }).click();
    await expect(page.getByRole("table", { name: "I tuoi prezzi a mano" })).toBeVisible();
    await page.getByLabel("Data", { exact: true }).fill("01/01/2099");
    await page.getByRole("button", { name: "Salva prezzo" }).click();
    await expect(page.getByText("La data non può essere nel futuro.")).toBeVisible();
    await expectAccessible(page, `manual prices, ${theme}`);
    await page.getByRole("button", { name: /^Elimina il prezzo del/ }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expectAccessible(page, `delete price dialog, ${theme}`);
    await page.keyboard.press("Escape");

    // Screens 11 and 10, and the search in a dialog of 10; nothing is saved.
    await page.goto("/holdings/add");
    await expect(page.getByRole("heading", { name: "Aggiungi un titolo", level: 1 })).toBeVisible();
    await page.getByLabel("Tipo").selectOption({ label: "Obbligazione" });
    await expectAccessible(page, `search page, ${theme}`);
    await page.goto("/holdings/existing");
    await page.getByLabel("Conto", { exact: true }).selectOption({ label: "Conto titoli (EUR)" });
    await page.getByLabel("Titolo, riga 1").selectOption({ label: "Esempio Azionario Mondo ETF · EUR" });
    await page.getByLabel("Quantità, riga 1").fill("10");
    await page.getByLabel("Prezzo medio di carico, riga 1").fill("100");
    await page.getByLabel("Titolo, riga 2").selectOption({ label: "Example Robotics Inc. · USD" });
    await page.getByLabel("Quantità, riga 3").fill("4");
    await page.getByRole("button", { name: "Salva 1 titolo" }).click();
    await expect(page.locator("table.add-rows tbody tr").first()).toContainText("1.000,00");
    await expect(page.getByText("Completa la riga o svuotala.").first()).toBeVisible();
    await expectAccessible(page, `add existing holdings, ${theme}`);
    await page.getByRole("button", { name: "Nuovo titolo per la riga 3" }).click();
    await expect(page.getByRole("dialog", { name: "Aggiungi un titolo" })).toBeVisible();
    await expectAccessible(page, `new security dialog of add existing, ${theme}`);
    await page.keyboard.press("Escape");

    await page.goto("/transactions");
    await expect(page.getByText("Righe 1–10 di 10", { exact: true })).toBeVisible();
    await expectAccessible(page, `transactions, ${theme}`);

    // The bulk dialogs; nothing is saved.
    await page.getByRole("checkbox", { name: /^Seleziona: Dividendo/ }).check();
    await page.getByRole("checkbox", { name: /^Seleziona: Versamento/ }).first().check();
    await page.getByRole("button", { name: "Modifica selezionate" }).click();
    await expect(page.getByRole("dialog")).toContainText("Vale per 1 delle 2");
    await expectAccessible(page, `bulk edit dialog, ${theme}`);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Elimina selezionate" }).click();
    await expectAccessible(page, `bulk delete dialog, ${theme}`);
    await page.keyboard.press("Escape");

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
    await page.getByRole("button", { name: "Nuovo titolo…" }).click();
    await page.getByRole("dialog").getByLabel("Tipo").selectOption({ label: "Obbligazione" });
    await expectAccessible(page, `new security dialog, ${theme}`);
    await page.keyboard.press("Escape");

    // The edit page of a coupon (income fields) and its delete dialog; nothing is saved.
    await page.goto("/transactions");
    await page.getByRole("link", { name: /^Modifica: Cedola/ }).click();
    await expect(page.getByLabel("Importo lordo (EUR)")).toHaveValue("75,00");
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
