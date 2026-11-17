import { expect, type Page, test } from "./fixtures";

import { apiPost, expectAccessible, logIn, newUser } from "./helpers";

// The page that updates the prices of every holding without a price source in one go. The
// seeded server runs with market data off, so no security there is ever "without a source"
// (an ISIN reads as "off", no ISIN as "manual"): the positions answer is rewritten in the
// browser to say so for the user's own security. The saving itself is the real API. Invented
// data only.

type Created = { id: string };

async function bondWithoutSource(page: Page) {
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const bond = await apiPost<Created>(page, "/assets", {
    name: "Esempio BTP Prezzi",
    asset_type: "BOND",
    quote_currency: "EUR",
    quote_unit: "PERCENT_OF_NOMINAL",
  });
  await apiPost(page, "/transactions", {
    account_id: account.id,
    type: "BUY",
    trade_date: "2026-03-02",
    asset_id: bond.id,
    affects_cash: false,
    quantity: "1000",
    unit_price: "98",
    gross_amount: "980.00",
    total_amount: "980.00",
  });
  await page.route("**/api/v1/portfolio/positions*", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { positions: { price_status: string }[] };
    for (const position of body.positions) position.price_status = "NOT_AVAILABLE";
    await route.fulfill({ response, json: body });
  });
  return { account, bond };
}

test("from the holdings list, update a price by hand and see it on the security", async ({
  page,
}) => {
  await logIn(page, await newUser("allprices"));
  const { bond } = await bondWithoutSource(page);

  await page.goto("/holdings");
  await page.getByRole("link", { name: "Update prices by hand" }).click();
  await expect(page.getByRole("heading", { name: "Update prices by hand", level: 1 })).toBeVisible();
  const row = page.getByRole("row", { name: /Esempio BTP Prezzi/ });
  await expect(row.getByText("No price yet")).toBeVisible();

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
    await bondWithoutSource(page);
    await page.goto("/holdings/prices");
    await expect(page.getByRole("heading", { name: "Update prices by hand", level: 1 })).toBeVisible();
    await expectAccessible(page, `manual prices of all, ${dark ? "dark" : "default"}`);
    // The error state too: a price that cannot be read.
    await page.getByLabel("Esempio BTP Prezzi: % of face value, clean price").fill("abc");
    await expectAccessible(page, `manual prices of all with an error, ${dark ? "dark" : "default"}`);
  });
}
