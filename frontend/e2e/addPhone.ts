import { expect, type Locator, type Page } from "@playwright/test";

import { apiPost, CSRF, logIn, newUser } from "./helpers";

// Add holdings, search and manual prices on phones (D-194): an investor with one account and
// invented securities, and the measures the specs check. Invented data only.

type Created = { id: string };

/** A new investor with one EUR account, an ETF, a bond and a stock; with `held`, each bought
 * (without moving cash), so the manual prices page lists them. */
export async function addInvestor(
  page: Page,
  prefix: string,
  { held = false, names = ["Phone Example World ETF", "Phone Example BTP 2031", "Phone Example Industries"] } = {},
) {
  await logIn(page, await newUser(prefix));
  const account = await apiPost<Created>(page, "/accounts", { name: "Phone account", currency: "EUR" });
  const make = (name: string, asset_type: string, quote_unit: string) =>
    apiPost<Created>(page, "/assets", { name, asset_type, quote_currency: "EUR", quote_unit });
  const etf = await make(names[0], "ETF", "CURRENCY");
  const bond = await make(names[1], "BOND", "PERCENT_OF_NOMINAL");
  const stock = await make(names[2], "STOCK", "CURRENCY");
  const bought: Record<string, string> = {};
  if (held) {
    for (const [asset, quantity, price] of [
      [etf, "10", "100"],
      [bond, "1000", "98"],
      [stock, "20", "12.50"],
    ] as const) {
      const gross = asset === bond ? "980.00" : (Number(quantity) * Number(price)).toFixed(2);
      const change = await apiPost<{ transactions: Created[] }>(page, "/transactions", {
        account_id: account.id, type: "BUY", trade_date: "2026-03-02", asset_id: asset.id,
        quantity, unit_price: price, gross_amount: gross, total_amount: gross, affects_cash: false,
      }); // prettier-ignore
      bought[asset.id] = change.transactions[0].id;
    }
  }
  return { account, etf, bond, stock, bought };
}

/** Deletes a security as another tab would: its only purchase, then the security itself. */
export async function deleteElsewhere(page: Page, transactionId: string, assetId: string) {
  const tx = await page.request.delete(`/api/v1/transactions/${transactionId}`, { headers: CSRF });
  expect(tx.ok(), `delete the purchase: ${tx.status()}`).toBe(true);
  const asset = await page.request.delete(`/api/v1/assets/${assetId}`, { headers: CSRF });
  expect(asset.ok(), `delete the security: ${asset.status()}`).toBe(true);
}

type Box = { left: number; top: number; right: number; bottom: number; width: number };

/** The boxes of a row card's cells, and of the card, rounded to whole pixels. */
export async function cardBoxes(row: Locator): Promise<{ card: Box; cells: Box[] }> {
  return row.evaluate((tr) => {
    const box = (el: Element) => {
      const b = el.getBoundingClientRect();
      return {
        left: Math.round(b.left),
        top: Math.round(b.top),
        right: Math.round(b.right),
        bottom: Math.round(b.bottom),
        width: Math.round(b.width),
      };
    };
    return { card: box(tr), cells: [...tr.children].map(box) };
  });
}

/** Every element of the page's main content wholly inside the window (a long name wraps). */
export async function outsideTheWindow(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    return [...document.querySelectorAll<HTMLElement>("main *")]
      .filter((el) => el.offsetParent !== null && !el.closest(".visually-hidden"))
      .filter((el) => {
        const b = el.getBoundingClientRect();
        return b.width > 0 && (b.left < -0.5 || b.right > width + 0.5);
      })
      .map((el) => `${el.tagName}.${el.className}`);
  });
}

/** Each of the scope's `.actions` buttons and links takes the whole width of its row. */
export async function fullWidthActions(page: Page, scope: string): Promise<boolean[]> {
  return page.locator(`${scope} .actions > :is(.button, a, button)`).evaluateAll((list) =>
    list
      .filter((el) => (el as HTMLElement).offsetParent !== null)
      .map((el) => {
        const own = el.getBoundingClientRect();
        const row = el.parentElement!.getBoundingClientRect();
        return Math.round(own.width) === Math.round(row.width);
      }),
  );
}
