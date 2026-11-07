import { expect, type Page } from "@playwright/test";

import { apiPost, CSRF, logIn, newUser } from "./helpers";

// Long invented security names (Obs 1, D-171), for long-names.spec.ts.

/** One word, no space: wraps only thanks to overflow-wrap: anywhere. */
export const ONE_WORD = "FixtureWorldwideDevelopedMarketsEquityIndexAccumulatingTrust";
/** A long phrase with spaces: wraps at the spaces. */
export const PHRASE =
  "Fixture Global Developed Markets Equity Index Fund UCITS ETF USD Accumulating Share Class Hedged to Euro";

/** A new investor with one account and the two long names, one up and one down. */
export async function longNamesUser(page: Page) {
  return holdingsUser(page, [ONE_WORD, PHRASE]);
}

/** A new investor with one account and two securities named `names`, one up and one down. */
export async function holdingsUser(page: Page, names: [string, string]) {
  const user = await newUser("longnames");
  await logIn(page, user);
  const account = await apiPost<{ id: string }>(page, "/accounts", {
    name: "Long names account",
    currency: "EUR",
  });
  await apiPost(page, "/transactions", {
    account_id: account.id, type: "DEPOSIT", trade_date: "2025-01-02", total_amount: "10000.00",
  }); // prettier-ignore
  const assets: Record<string, string> = {};
  for (const [name, price] of [[names[0], "112.40"], [names[1], "91.75"]]) {
    const asset = await apiPost<{ id: string }>(page, "/assets", {
      name, asset_type: "ETF", quote_currency: "EUR", quote_unit: "CURRENCY",
    }); // prettier-ignore
    assets[name] = asset.id;
    await apiPost(page, "/transactions", {
      account_id: account.id, type: "BUY", trade_date: "2025-01-10", asset_id: asset.id,
      quantity: "20", unit_price: "100.00", gross_amount: "2000.00", fees: "0.00",
      total_amount: "2000.00",
    }); // prettier-ignore
    const priced = await page.request.put(`/api/v1/assets/${asset.id}/prices`, {
      data: { day: "2025-12-30", close: price },
      headers: CSRF,
    });
    expect(priced.ok(), "manual price").toBe(true);
  }
  return assets;
}
