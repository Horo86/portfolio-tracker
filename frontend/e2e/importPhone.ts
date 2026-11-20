import { expect, type Page } from "@playwright/test";

import { apiPost, logIn, newUser } from "./helpers";
import { ONE_WORD, PHRASE } from "./longNames";

// The import on phones and tablets (D-193): invented files and the synthetic Directa fixture,
// for phone.spec.ts, tablet.spec.ts and long-names.spec.ts.

export const DIRECTA_FIXTURE = "../tests/fixtures/directa/directa_synthetic_2024.xlsx";

/** A file name of one long word, no space. */
export const LONG_FILE =
  "movements-of-an-example-account-exported-from-the-first-of-january-to-the-thirty-first-of-december-final.csv";

const CSV_HEAD =
  "account,account_currency,trade_date,type,isin,ticker,asset_name,quantity,unit_price,gross_amount,fees,taxes,total_amount,external_ref";

/** An invented CSV: a security named with one long word and one named with a long phrase, an
 * account without a currency and a sale of more than is held (the blocking card and notes),
 * a line of an unknown type. */
export const LONG_CSV = [
  CSV_HEAD,
  "Conto CSV,,2026-03-02,DEPOSIT,,,,,,,,,1000.00,L-1",
  `Conto CSV,,2026-03-03,BUY,ZZ0000002012,,${ONE_WORD},10,12.50,125.00,1.00,,126.00,L-2`,
  `Conto CSV,,2026-03-04,BUY,ZZ0000002020,,${PHRASE},5,20.00,100.00,1.00,,101.00,L-3`,
  `Conto CSV,,2026-03-05,SELL,ZZ0000002012,,${ONE_WORD},30,13.00,390.00,1.00,,389.00,L-4`,
  "Conto CSV,,2026-03-06,PIPPO,,,,,,,,,10.00,L-5",
].join("\n");

/** Two accounts, one named with one long word: the summary's table of accounts. */
export const TWO_ACCOUNTS_CSV = [
  CSV_HEAD,
  "Conto Uno,EUR,2026-02-02,DEPOSIT,,,,,,,,,1000.00,T-1",
  "Conto Uno,EUR,2026-02-09,WITHDRAWAL,,,,,,,,,100.00,T-2",
  `${ONE_WORD},EUR,2026-02-02,DEPOSIT,,,,,,,,,2000.00,T-3`,
].join("\n");

/** A new investor with an EUR account for the Directa fixture. */
export async function importInvestor(page: Page, prefix: string): Promise<void> {
  await logIn(page, await newUser(prefix));
  await apiPost(page, "/accounts", { name: "Conto Directa", currency: "EUR" });
}

/** Chooses a file made in the browser, with the given text. */
export async function chooseMadeFile(page: Page, name: string, text: string): Promise<void> {
  await page.getByLabel("File to import").evaluate(
    (input: HTMLInputElement, file) => {
      const data = new DataTransfer();
      data.items.add(new File([file.text], file.name, { type: "text/csv" }));
      input.files = data.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { name, text },
  );
}

/** From the import page: the Directa fixture into "Conto Directa", up to its preview, with the
 * types its new securities need (the bond is proposed, D-102). */
export async function previewDirecta(page: Page): Promise<void> {
  await page.goto("/import");
  await page.getByLabel("File to import").setInputFiles(DIRECTA_FIXTURE);
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await page.getByLabel("Import into account").selectOption({ label: "Conto Directa" });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("tablist", { name: "Lines by category" })).toBeVisible();
  const asset = (name: string) => page.locator(".import-asset", { hasText: name });
  await asset("TEST ETF WORLD UCITS").getByLabel("Type").selectOption({ label: "ETF" });
  await asset("TEST AZIONE SPA").getByLabel("Type").selectOption({ label: "Stock" });
  await expect(page.locator(".import-blocking")).toHaveCount(0);
}

/** From the import page: an invented CSV, up to its preview. */
export async function previewCsv(page: Page, name: string, text: string): Promise<void> {
  await page.goto("/import");
  await chooseMadeFile(page, name, text);
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await expect(page.getByRole("tablist", { name: "Lines by category" })).toBeVisible();
}

/** Where the confirmation bar is after scrolling the window to `y`, against the bottom bar. */
export async function confirmBarAt(page: Page, y: number) {
  return page.evaluate((top) => {
    window.scrollTo(0, top);
    const bar = document.querySelector<HTMLElement>(".confirm-bar")!;
    const box = bar.getBoundingClientRect();
    const nav = document.querySelector<HTMLElement>(".bottom-nav")!.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    const navHit = document.elementFromPoint(nav.left + nav.width / 2, nav.top + nav.height / 2);
    return {
      scrolled: window.scrollY,
      position: getComputedStyle(bar).position,
      top: box.top,
      bottom: box.bottom,
      navTop: nav.top,
      onTop: bar.contains(hit),
      navOnTop: !!navHit?.closest(".bottom-nav"),
    };
  }, y);
}

/** The cells of the lines' cards that are wider than their card. */
export async function cellsOutsideTheirCards(page: Page): Promise<string[]> {
  return page.locator(".import-table tbody tr").evaluateAll((rows) =>
    rows.flatMap((row) => {
      const card = row.getBoundingClientRect();
      return [...row.children]
        .filter((cell) => {
          const box = cell.getBoundingClientRect();
          return box.right > card.right + 0.5 || box.left < card.left - 0.5;
        })
        .map((cell) => cell.textContent ?? "");
    }),
  );
}
