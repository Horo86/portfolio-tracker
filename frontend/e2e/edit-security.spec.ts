import { expect, type Page, test } from "./fixtures";

import { apiPost, CSRF, expectAccessible, logIn, newUser } from "./helpers";

// Editing and deleting a security (D-150), and reaching those no transaction uses. Invented
// securities and amounts only; market data is off on this server.

type Created = { id: string };

async function buy(page: Page, account: string, asset: string) {
  await apiPost(page, "/transactions", {
    account_id: account,
    type: "BUY",
    trade_date: "2026-03-02",
    asset_id: asset,
    quantity: "10",
    unit_price: "100",
    gross_amount: "1000.00",
    total_amount: "1000.00",
    affects_cash: false,
  });
}

test("a security in use: its locked fields are shown and named, its name changes", async ({ page }) => {
  await logIn(page, await newUser("edit"));
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const asset = await apiPost<Created>(page, "/assets", {
    name: "Esempio Modifica ETF", asset_type: "ETF", quote_currency: "EUR",
    quote_unit: "CURRENCY", isin: "ZZ0000087047" }); // prettier-ignore
  await buy(page, account.id, asset.id);

  await page.goto(`/holdings/${asset.id}`);
  await page.getByRole("button", { name: "Edit security" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit security" });
  await expect(dialog).toContainText(
    "Type, currency, price unit and ISIN can change only while no transaction uses the security.",
  );
  for (const label of ["Type", "Currency", "ISIN (optional)", "The price is expressed in"]) {
    await expect(dialog.getByLabel(label)).toBeDisabled();
  }
  await expect(dialog.getByLabel("ISIN (optional)")).toHaveValue("ZZ0000087047");
  await expect(dialog).toContainText("Only a security with no transactions can be deleted.");
  await expect(dialog.getByRole("button", { name: "Delete security" })).toHaveCount(0);

  await dialog.getByLabel("Name").fill("Esempio Rinominato ETF");
  await dialog.getByLabel("Symbol (optional)").fill("ZREN");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Security updated.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Esempio Rinominato ETF", level: 1 })).toBeVisible();
  await expect(page.getByRole("list", { name: "Security details" })).toContainText("ZREN");
  await page.goto("/holdings");
  await expect(page.getByRole("row", { name: /Esempio Rinominato ETF/ })).toBeVisible();
});

test("a security in use without an ISIN gets one; its manual price stays (D-171)", async ({ page }) => {
  await logIn(page, await newUser("addisin"));
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const asset = await apiPost<Created>(page, "/assets", {
    name: "Esempio Senza ISIN", asset_type: "ETF", quote_currency: "EUR",
    quote_unit: "CURRENCY" }); // prettier-ignore
  await buy(page, account.id, asset.id);
  const priced = await page.request.put(`/api/v1/assets/${asset.id}/prices`, {
    data: { day: "2026-03-03", close: "104.00" },
    headers: CSRF,
  });
  expect(priced.ok()).toBe(true);

  await page.goto(`/holdings/${asset.id}`);
  await page.getByRole("button", { name: "Edit security" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit security" });
  await expect(dialog).toContainText("An ISIN can still be added.");
  for (const label of ["Type", "Currency", "The price is expressed in"]) {
    await expect(dialog.getByLabel(label)).toBeDisabled();
  }
  await dialog.getByLabel("ISIN (optional)").fill("ZZ0000087013");
  await expect(dialog).toContainText("Your manual prices stay and still count on their own days");
  await expectAccessible(page, "the edit dialog adding an ISIN to a security in use");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Security updated.")).toBeVisible();
  await expect(page.getByRole("list", { name: "Security details" })).toContainText("ZZ0000087013");
  // The amounts are as before, and the manual price is still the one in use.
  const value = page.getByRole("region", { name: "Position value" });
  await expect(value).toContainText("€1,040.00");
  await page.goto(`/holdings/${asset.id}/prices`);
  await expect(page.getByRole("table", { name: "Your manual prices" }).getByRole("row")).toHaveCount(2);
});

test("securities with no transactions: listed on Titoli, opened, refused a currency over prices, deleted", async ({
  page,
}) => {
  await logIn(page, await newUser("unused", { language: "it" }));
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto prova", currency: "EUR" });
  const spare = await apiPost<Created>(page, "/assets", {
    name: "Esempio Da Eliminare", asset_type: "ETF", quote_currency: "EUR",
    quote_unit: "CURRENCY" }); // prettier-ignore
  const held = await apiPost<Created>(page, "/assets", {
    name: "Esempio Posseduto", asset_type: "STOCK", quote_currency: "EUR",
    quote_unit: "CURRENCY" }); // prettier-ignore
  await buy(page, account.id, held.id);
  const priced = await page.request.put(`/api/v1/assets/${spare.id}/prices`, {
    data: { day: "2026-03-02", close: "101.50" },
    headers: CSRF,
  });
  expect(priced.ok()).toBe(true);

  await page.goto(`/holdings?account=${account.id}`);
  await expect(page.getByRole("row", { name: /Esempio Posseduto/ })).toBeVisible();
  await expect(page.getByText("1 titolo senza transazioni")).toBeVisible();

  // "Delete all" leaves the other one with no transactions: back on Titoli, the line counts it.
  await page.getByRole("link", { name: "Esempio Posseduto" }).click();
  await page.getByRole("button", { name: "Elimina tutte le transazioni del titolo…" }).click();
  const all = page.getByRole("dialog");
  await all.getByLabel("Scrivi il nome del titolo per confermare").fill("Esempio Posseduto");
  await all.getByRole("button", { name: "Elimina 1 transazione" }).click();
  await expect(page.getByRole("heading", { name: "Titoli", level: 1 })).toBeVisible();
  await expect(page.getByText("2 titoli senza transazioni")).toBeVisible();

  await page.getByRole("button", { name: "Mostra" }).click();
  const list = page.getByRole("list", { name: "2 titoli senza transazioni" });
  await expect(list).toBeFocused();
  await expect(list.getByRole("listitem")).toHaveText([/Esempio Da Eliminare/, /Esempio Posseduto/]);
  await expectAccessible(page, "holdings with the securities with no transactions");
  await list.getByRole("listitem").filter({ hasText: "Esempio Da Eliminare" }).getByRole("link", { name: "Apri" }).click();

  await expect(page.getByRole("heading", { name: "Esempio Da Eliminare", level: 1 })).toBeVisible();
  await expect(page.getByText("Questo titolo non ha transazioni.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Prezzi a mano", exact: true })).toBeVisible();
  await expectAccessible(page, "detail of a security with no transactions");

  // Its manual price would be read in the new currency: refused.
  await page.getByRole("button", { name: "Modifica titolo" }).click();
  const edit = page.getByRole("dialog", { name: "Modifica titolo" });
  await expect(edit.getByLabel("Valuta")).toBeEnabled();
  await expectAccessible(page, "edit dialog of a security with no transactions");
  await edit.getByLabel("Valuta").selectOption("USD");
  await edit.getByRole("button", { name: "Salva" }).click();
  await expect(edit.getByRole("alert")).toHaveText(
    "Hai prezzi a mano per questo titolo: eliminali prima di cambiare valuta o unità.",
  );

  await edit.getByRole("button", { name: "Elimina titolo" }).click();
  const confirm = page.getByRole("dialog", { name: "Eliminare «Esempio Da Eliminare»?" });
  await expect(confirm).toContainText(
    "Il titolo non ha transazioni. Verrà eliminato anche il tuo prezzo a mano.",
  );
  await expectAccessible(page, "delete security dialog");
  await confirm.getByRole("button", { name: "Elimina titolo" }).click();
  await expect(page.getByText("«Esempio Da Eliminare» eliminato.")).toBeVisible();
  await expect(page.getByText("1 titolo senza transazioni")).toBeVisible();
  await page.getByRole("button", { name: "Mostra" }).click();
  await expect(page.getByRole("list", { name: "1 titolo senza transazioni" }).getByRole("listitem")).toHaveText([
    /Esempio Posseduto/,
  ]);
  expect((await page.request.get(`/api/v1/assets/${spare.id}`)).status()).toBe(404);
});
