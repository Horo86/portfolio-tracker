import { expect, type Page, test } from "./fixtures";

import { apiPost, expectAccessible, logIn, newUser } from "./helpers";

// The page that updates, in one go, the prices of the holdings whose prices are entered by
// hand. The seeded server runs with market data off, so a real security without an ISIN has
// the status "manual" and a real one with an ISIN has "off": both are listed, no status is
// faked. Invented data only.

type Created = { id: string };

async function holdings(page: Page) {
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const buy = async (asset: Record<string, string>) => {
    const created = await apiPost<Created>(page, "/assets", asset);
    await apiPost(page, "/transactions", {
      account_id: account.id,
      type: "BUY",
      trade_date: "2026-03-02",
      asset_id: created.id,
      affects_cash: false,
      quantity: "1000",
      unit_price: "98",
      gross_amount: "980.00",
      total_amount: "980.00",
    });
    return created;
  };
  const bond = await buy({
    name: "Esempio BTP Prezzi",
    asset_type: "BOND",
    quote_currency: "EUR",
    quote_unit: "PERCENT_OF_NOMINAL",
    isin: "ZZ0000087054", // with an ISIN and market data off: "off"
  });
  const other = await buy({
    name: "Esempio Titolo Senza ISIN",
    asset_type: "STOCK",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
  });
  return { account, bond, other };
}

test("from the holdings list, update a price by hand and see it on the security", async ({
  page,
}) => {
  await logIn(page, await newUser("allprices"));
  const { bond } = await holdings(page);

  await page.goto("/holdings");
  await page.getByRole("link", { name: "Update prices by hand" }).click();
  await expect(page.getByRole("heading", { name: "Update prices by hand", level: 1 })).toBeVisible();
  const row = page.getByRole("row", { name: /Esempio BTP Prezzi/ });
  await expect(row.getByText("No price yet")).toBeVisible();
  await expect(page.getByRole("link", { name: "Esempio Titolo Senza ISIN" })).toBeVisible();

  await page.getByLabel("Esempio BTP Prezzi: % of face value, clean price").fill("99.4");
  await page.getByRole("button", { name: "Save prices" }).click();
  await expect(row.getByRole("status")).toHaveText("Saved");
  await expect(page.getByRole("button", { name: "Save prices" })).toBeDisabled();

  await page.goto(`/holdings/${bond.id}`);
  await expect(page.getByText("Manual price", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Position value" })).toContainText("€994.00");
});

for (const dark of [false, true]) {
  test(`no axe violation on the page, ${dark ? "dark" : "default"} theme`, async ({ page }) => {
    if (dark) await page.emulateMedia({ colorScheme: "dark" });
    await logIn(page, await newUser("allaxe"));
    await holdings(page);
    await page.goto("/holdings/prices");
    await expect(page.getByRole("heading", { name: "Update prices by hand", level: 1 })).toBeVisible();
    await expectAccessible(page, `manual prices of all, ${dark ? "dark" : "default"}`);
    // The error state too: a price that cannot be read.
    await page.getByLabel("Esempio BTP Prezzi: % of face value, clean price").fill("abc");
    await expectAccessible(page, `manual prices of all with an error, ${dark ? "dark" : "default"}`);
  });
}
