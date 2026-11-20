import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

import { ADMIN, apiPatch, DEMO, expectAccessible, logIn, newUser } from "./helpers";
import { DIRECTA_FIXTURE, importInvestor } from "./importPhone";
import {
  confirmEnrolment,
  enrolToQr,
  fakeFirstRun,
  fakeTwoFactorObligation,
  firstRunToLanguage,
} from "./remainingPhone";

// axe (WCAG 2.1 A and AA) on the screens of this slice, in each of the four themes and in dark
// (D-176): their colors differ, so contrast is checked in all of them. The demo user of the
// development data has real-looking (invented) accounts and figures, in Italian.
type Walk = { theme: string; colours: string; dark?: boolean };
const WALKS: Walk[] = [
  ...(["sobrio", "moderno", "ambra", "grafite"] as const).map((name) => ({
    theme: name,
    colours: name,
  })),
  // A device that prefers dark, the browser on "same as the device": dark over Moderno.
  { theme: "scuro", colours: "moderno", dark: true },
];

for (const { theme, colours, dark } of WALKS) {
  test(`no axe violation in the ${theme} theme`, async ({ page }) => {
    // One walk through every screen per theme: about 27 s on a quiet machine since screen 12
    // joined it, too close to the default 30 s on a busy runner.
    test.setTimeout(60_000);
    if (dark) await page.emulateMedia({ colorScheme: "dark" });
    await page.addInitScript((name) => window.localStorage.setItem("pt.theme", name), colours);
    await page.goto("/login");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expectAccessible(page, `login, ${theme}`);

    await logIn(page, DEMO);
    await apiPatch(page, "/me", { theme: colours });
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

// On a phone (D-195): every screen and its dialogs at 390 px in Moderno, the layout of the
// compact shell and the cards; then a short walk in dark. The demo user in Italian; fresh users
// (in English) for what would change the development data. Nothing of the demo is saved.
test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  async function more(page: Page, name: string) {
    await page.getByRole("navigation", { name: /^(Menu principale|Main menu)$/ })
      .getByRole("button", { name }).click(); // prettier-ignore
    await expect(page.getByRole("dialog", { name })).toBeVisible();
  }

  /** Ends the session; the next login starts from the browser's language, as logIn expects. */
  async function leave(page: Page) {
    await page.context().clearCookies();
    await page.evaluate(() => localStorage.removeItem("pt.language"));
  }

  test("phone: no axe violation on every screen in Moderno", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/login");
    await expectAccessible(page, "phone: login");
    await logIn(page, DEMO);
    await apiPatch(page, "/me", { theme: "moderno" });
    await page.goto("/");
    await expect(page.locator(".chart-donut svg")).toBeVisible();
    await expectAccessible(page, "phone: dashboard");
    await more(page, "Altro");
    await expectAccessible(page, "phone: the menu");
    await page.keyboard.press("Escape");

    await page.goto("/holdings");
    await expect(page.getByRole("row", { name: /Example Robotics/ })).toBeVisible();
    await expectAccessible(page, "phone: holdings");
    await page.getByRole("link", { name: "Esempio Titolo di Stato 2031" }).click();
    await expect(page.getByRole("region", { name: "Cedole ricevute" })).toBeVisible();
    await expectAccessible(page, "phone: holding detail");
    await page.getByRole("button", { name: "Modifica titolo" }).click();
    await expectAccessible(page, "phone: edit security dialog");
    await page.keyboard.press("Escape");
    await page.getByRole("link", { name: "Prezzi a mano", exact: true }).click();
    await expect(page.getByRole("table", { name: "I tuoi prezzi a mano" })).toBeVisible();
    await expectAccessible(page, "phone: manual prices");
    await page.goto("/holdings/prices");
    await expectAccessible(page, "phone: every manual price");
    await page.goto("/holdings/add");
    await expect(page.getByRole("heading", { name: "Aggiungi un titolo", level: 1 })).toBeVisible();
    await expectAccessible(page, "phone: search and create");
    await page.goto("/holdings/existing");
    await page.getByLabel("Conto", { exact: true }).selectOption({ label: "Conto titoli (EUR)" });
    await page.getByLabel("Titolo, riga 1").selectOption({ label: "Esempio Azionario Mondo ETF · EUR" });
    await expectAccessible(page, "phone: add existing holdings");
    await page.getByRole("button", { name: "Nuovo titolo per la riga 1" }).click();
    await expectAccessible(page, "phone: new security dialog");
    await page.keyboard.press("Escape");

    await page.goto("/transactions");
    await expect(page.getByText("Righe 1–10 di 10", { exact: true })).toBeVisible();
    await page.getByRole("checkbox", { name: /^Seleziona: Dividendo/ }).check();
    await expectAccessible(page, "phone: transactions, one selected");
    await page.getByRole("button", { name: "Modifica selezionate" }).click();
    await expectAccessible(page, "phone: bulk edit dialog");
    await page.keyboard.press("Escape");
    await page.getByRole("link", { name: "Aggiungi transazione" }).click();
    await page.getByRole("radio", { name: "Vendita" }).check();
    await expectAccessible(page, "phone: transaction form");
    await page.goto("/transactions");
    await page.getByRole("link", { name: /^Modifica: Cedola/ }).click();
    await expect(page.getByLabel("Importo lordo (EUR)")).toHaveValue("75,00");
    await expectAccessible(page, "phone: edit page");
    await page.getByRole("button", { name: "Elimina…" }).click();
    await expectAccessible(page, "phone: delete dialog");
    await page.keyboard.press("Escape");

    await page.goto("/accounts");
    await expect(page.getByRole("region", { name: "Conto estero" })).toContainText("$");
    await expectAccessible(page, "phone: accounts");
    await page.getByRole("region", { name: "Conto titoli" }).getByRole("button", { name: "Modifica" }).click();
    await expectAccessible(page, "phone: edit account dialog");
    await page.getByRole("button", { name: "Elimina conto…" }).click();
    await expectAccessible(page, "phone: delete account dialog");
    await page.keyboard.press("Escape");
    await page.goto("/settings");
    await expect(page.getByRole("list", { name: "Sessioni attive" })).toBeVisible();
    await expectAccessible(page, "phone: settings");
    await page.getByRole("button", { name: "Cambia password" }).click();
    await expectAccessible(page, "phone: password dialog");
    await page.keyboard.press("Escape");
    await page.goto("/about");
    await expectAccessible(page, "phone: about");
    await page.getByRole("button", { name: /Note di rilascio$/ }).click();
    await expectAccessible(page, "phone: release notes");
    await page.keyboard.press("Escape");
    await page.goto("/no/such/page");
    await expectAccessible(page, "phone: not found");
    await leave(page);

    await logIn(page, ADMIN);
    await page.goto("/admin");
    await expect(page.getByRole("table", { name: "Utenti" })).toBeVisible();
    await expectAccessible(page, "phone: administration");
    await leave(page);

    // The import up to its summary, as a fresh investor (it saves).
    await importInvestor(page, "a11y-phone-import");
    await page.goto("/import");
    await expectAccessible(page, "phone: import step 1");
    await page.getByLabel("File to import").setInputFiles(DIRECTA_FIXTURE);
    await page.getByRole("button", { name: "Continue to the preview" }).click();
    await expect(page.getByLabel("Import into account")).toBeVisible();
    await expectAccessible(page, "phone: import account step");
    await page.getByLabel("Import into account").selectOption({ label: "Conto Directa" });
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(page.getByRole("tablist", { name: "Lines by category" })).toBeVisible();
    await expectAccessible(page, "phone: import preview");
    const asset = (name: string) => page.locator(".import-asset", { hasText: name });
    await asset("TEST ETF WORLD UCITS").getByLabel("Type").selectOption({ label: "ETF" });
    await asset("TEST AZIONE SPA").getByLabel("Type").selectOption({ label: "Stock" });
    await page.getByRole("button", { name: /^Confirm the import of \d+ transactions$/ }).click();
    await expect(page.getByRole("heading", { name: /^Done: / })).toBeVisible();
    await expectAccessible(page, "phone: import summary");
    await leave(page);

    // Two-step verification: the dialog, the recovery codes, the login's code step.
    const user = await newUser("a11y-phone-2fa");
    await logIn(page, user);
    await page.goto("/settings");
    await page.getByRole("button", { name: "Turn on 2FA" }).click();
    const dialog = page.getByRole("dialog", { name: "Turn on two-step verification" });
    await expectAccessible(page, "phone: 2FA dialog, password");
    const secret = await enrolToQr(dialog, user.password);
    await expectAccessible(page, "phone: 2FA dialog, QR code");
    await confirmEnrolment(dialog, secret);
    await expectAccessible(page, "phone: 2FA dialog, recovery codes");
    await dialog.getByRole("button", { name: "I saved the codes" }).click();
    await leave(page);
    await page.goto("/login");
    await page.getByLabel("Username").fill(user.username);
    await page.getByLabel("Password").fill(user.password);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page.getByRole("heading", { name: "Two-step verification", level: 1 })).toBeVisible();
    await expectAccessible(page, "phone: the login's code step");
    await leave(page);

    // The forced password change, and the forced enrolment (`/me` answered in the browser:
    // the real obligation needs an empty server, two-factor.spec.ts; its endpoints are real).
    const temporary = await newUser("a11y-phone-forced", { temporary: true });
    await page.goto("/login");
    await page.getByLabel("Username").fill(temporary.username);
    await page.getByLabel("Password").fill(temporary.password);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page.getByRole("heading", { name: "Change your temporary password" })).toBeVisible();
    await expectAccessible(page, "phone: forced password change");
    await leave(page);
    const enrolling = await newUser("a11y-phone-forced-2fa");
    await logIn(page, enrolling);
    await fakeTwoFactorObligation(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Turn on two-step verification", level: 1 })).toBeVisible();
    await expectAccessible(page, "phone: forced enrolment");
    const forced = await enrolToQr(page, enrolling.password);
    await expectAccessible(page, "phone: forced enrolment, QR code");
    await confirmEnrolment(page, forced);
    await expectAccessible(page, "phone: forced enrolment, recovery codes");
    await page.unroute("**/api/v1/me");
    await leave(page);

    // The first run (the setup status answered in the browser: the real one needs an empty
    // server, first-run.spec.ts); nothing is submitted.
    await fakeFirstRun(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Welcome", exact: true })).toBeVisible();
    await expectAccessible(page, "phone: first run");
    await firstRunToLanguage(page);
    await expectAccessible(page, "phone: first run, language step");
  });

  test("phone: no axe violation in dark, a short walk", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/login");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "scuro");
    await expectAccessible(page, "phone, dark: login");
    await logIn(page, DEMO);
    await apiPatch(page, "/me", { theme: "moderno" });
    await page.goto("/");
    await expect(page.locator(".chart-donut svg")).toBeVisible();
    await expectAccessible(page, "phone, dark: dashboard");
    await more(page, "Altro");
    await expectAccessible(page, "phone, dark: the menu");
    await page.keyboard.press("Escape");
    for (const path of ["/holdings", "/transactions", "/accounts", "/settings", "/about"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await expectAccessible(page, `phone, dark: ${path}`);
    }
    await page.getByRole("button", { name: /Note di rilascio$/ }).click();
    await expectAccessible(page, "phone, dark: release notes");
  });
});

test.afterAll(async ({ browser }) => {
  // Leave the demo user as the development data made it.
  const page = await browser.newPage();
  await logIn(page, DEMO);
  await apiPatch(page, "/me", { theme: "moderno" });
  await page.close();
});
