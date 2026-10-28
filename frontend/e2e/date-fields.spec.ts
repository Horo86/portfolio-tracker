import { expect, type Page, test } from "@playwright/test";

import { apiPost, logIn, newUser } from "./helpers";

// A day typed with the keyboard in the app's language (M5 slice 6): gg/mm/aaaa, dd/mm/yyyy,
// whatever the browser's own language. Invented accounts and securities only.

type Created = { id: string };

const TEXTS = {
  en: {
    add: "Add transaction",
    deposit: "Deposit",
    date: "Date",
    amount: "Amount (EUR)",
    calendar: "Pick from the calendar: Date",
    wrong: "Enter the date as dd/mm/yyyy.",
    impossible: "This date does not exist.",
    saved: "Transaction saved.",
    manualPrices: "Manual prices",
    price: "Price (EUR)",
  },
  it: {
    add: "Aggiungi transazione",
    deposit: "Versamento",
    date: "Data",
    amount: "Importo (EUR)",
    calendar: "Scegli dal calendario: Data",
    wrong: "Scrivi la data come gg/mm/aaaa.",
    impossible: "Questa data non esiste.",
    saved: "Transazione salvata.",
    manualPrices: "Prezzi a mano",
    price: "Prezzo (EUR)",
  },
} as const;

function shown(day: string, language: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Intl.DateTimeFormat(language, { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, date)),
  );
}

/** Replaces the field's text with what is typed on the keyboard. */
async function typeIn(page: Page, label: string, text: string) {
  await page.getByLabel(label, { exact: true }).focus();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.press("Backspace");
  await page.keyboard.type(text);
}

for (const language of ["en", "it"] as const) {
  const t = TEXTS[language];

  test(`a date typed with the keyboard in the transaction form (${language})`, async ({ page }) => {
    await logIn(page, await newUser("dates", { language }));
    await apiPost<Created>(page, "/accounts", { name: "Conto date", currency: "EUR" });
    await page.goto("/transactions");
    await page.getByRole("link", { name: t.add }).click();
    await page.getByRole("radio", { name: t.deposit }).check();
    const date = page.getByLabel(t.date, { exact: true });

    await typeIn(page, t.date, "31/02/2026");
    await page.keyboard.press("Tab"); // leaves the field: to its calendar button
    await expect(page.getByRole("button", { name: t.calendar, exact: true })).toBeFocused();
    await expect(page.getByText(t.impossible)).toBeVisible();
    await expect(date).toHaveAttribute("aria-invalid", "true");

    await typeIn(page, t.date, "5/3/26");
    await page.keyboard.press("Tab");
    await expect(page.getByText(t.wrong)).toBeVisible();

    await typeIn(page, t.date, "5.3.2026");
    await page.keyboard.press("Tab");
    await expect(page.getByText(t.wrong)).toHaveCount(0);
    await expect(date).not.toHaveAttribute("aria-invalid");
    await typeIn(page, t.amount, "250");
    await page.keyboard.press("Enter"); // submits the form

    await expect(page.getByRole("status").filter({ hasText: t.saved })).toBeVisible();
    await expect(page).toHaveURL("/transactions");
    await expect(page.getByRole("row").nth(1)).toContainText(shown("2026-03-05", language));
  });

  test(`a date typed with the keyboard in the manual price form (${language})`, async ({ page }) => {
    await logIn(page, await newUser("dates", { language }));
    const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
    const asset = await apiPost<Created>(page, "/assets", {
      name: "Esempio Date ETF",
      asset_type: "ETF",
      quote_currency: "EUR",
      quote_unit: "CURRENCY",
    });
    await apiPost(page, "/transactions", {
      account_id: account.id, type: "DEPOSIT", trade_date: "2026-01-05", total_amount: "1000.00",
    }); // prettier-ignore
    await apiPost(page, "/transactions", {
      account_id: account.id, type: "BUY", trade_date: "2026-02-02", asset_id: asset.id,
      quantity: "10", unit_price: "100", gross_amount: "1000.00", total_amount: "1000.00",
    }); // prettier-ignore
    await page.goto(`/holdings/${asset.id}?account=${account.id}`);
    await page.getByRole("link", { name: t.manualPrices, exact: true }).click();

    await typeIn(page, t.date, "1-3-2026");
    await page.keyboard.press("Tab");
    await typeIn(page, t.price, language === "it" ? "101,5" : "101.5");
    await page.keyboard.press("Enter");

    const day = shown("2026-03-01", language);
    const saved = language === "it" ? `Prezzo del ${day} salvato.` : `Price of ${day} saved.`;
    await expect(page.getByText(saved)).toBeVisible();
  });
}
