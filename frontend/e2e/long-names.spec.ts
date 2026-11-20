import { expect, type Page, test } from "./fixtures";

import { expectAccessible } from "./helpers";
import { cellsOutsideTheirCards, importInvestor, LONG_CSV, LONG_FILE, previewCsv } from "./importPhone";
import { longNamesUser, ONE_WORD, PHRASE } from "./longNames";

// Long security names on the dashboard (Obs 1, D-171): real names are long, sometimes one long
// word. Both invented names (longNames.ts) must wrap inside their cards, the figures beside them stay on
// one line, and no card grows past its column. Layout needs a real browser, hence this spec.

test.beforeAll(() => {
  expect(ONE_WORD).not.toContain(" ");
  expect(ONE_WORD.length).toBe(60);
  expect(PHRASE.length).toBeGreaterThan(100);
});

type Layout = {
  cards: { name: string; scroll: number; client: number }[];
  tableRight: number;
  cardRight: number;
  names: { text: string; overflows: boolean; lines: number }[];
  figureLines: number[];
  pills: { inside: boolean; lines: number }[];
};

/** Measures, in the page, what the user would see overflow. */
async function layout(page: Page): Promise<Layout> {
  return page.evaluate(() => {
    const lines = (element: Element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const tops = new Set([...range.getClientRects()].map((rect) => Math.round(rect.top)));
      return tops.size;
    };
    const cards = [...document.querySelectorAll<HTMLElement>(".dashboard-columns > .card")];
    const allocation = cards.find((card) => card.querySelector(".asset-shares")) as HTMLElement;
    const table = allocation.querySelector(".asset-shares") as HTMLElement;
    const names = [
      ...table.querySelectorAll<HTMLElement>("tbody th a"),
      ...document.querySelectorAll<HTMLElement>(".ranked-name a"),
    ];
    return {
      cards: cards.map((card) => ({
        name: card.querySelector("h2")?.textContent ?? "",
        scroll: card.scrollWidth,
        client: card.clientWidth,
      })),
      tableRight: table.getBoundingClientRect().right,
      cardRight: allocation.getBoundingClientRect().right,
      names: names.map((link) => ({
        text: link.textContent ?? "",
        overflows: link.scrollWidth > (link.parentElement as HTMLElement).clientWidth + 1,
        lines: lines(link),
      })),
      figureLines: [...table.querySelectorAll("tbody td.num")].map(lines),
      pills: [...document.querySelectorAll<HTMLElement>(".ranked-list li")].map((li) => {
        const pill = li.querySelector(".gain-pill") as HTMLElement;
        return {
          inside: pill.getBoundingClientRect().right <= li.getBoundingClientRect().right + 0.5,
          lines: lines(pill),
        };
      }),
    };
  });
}

// At 1440 the dashboard has two columns, so the allocation card is narrow and both names must
// wrap (the single word only through overflow-wrap); at 1024 it has one wide column.
for (const size of [
  { width: 1440, height: 900, mustWrap: true },
  { width: 1024, height: 768, mustWrap: false },
]) {
  test(`long names wrap inside their cards at ${size.width}×${size.height}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await longNamesUser(page);
    await page.goto("/");
    const allocation = page.getByRole("table", { name: "By security" });
    await expect(allocation.getByRole("link", { name: ONE_WORD })).toBeVisible();
    await expect(allocation.getByRole("link", { name: PHRASE })).toBeVisible();
    const best = page.getByRole("region", { name: "Best and worst securities" });
    await expect(best.getByRole("link", { name: ONE_WORD })).toBeVisible();

    const seen = await layout(page);

    for (const card of seen.cards) {
      expect(card.scroll, `the card "${card.name}" overflows`).toBeLessThanOrEqual(card.client);
    }
    expect(seen.tableRight).toBeLessThanOrEqual(seen.cardRight + 0.5);
    for (const name of seen.names) {
      expect(name.overflows, `${name.text} overflows its cell`).toBe(false);
      expect([ONE_WORD, PHRASE]).toContain(name.text); // shown whole, never cut
    }
    if (size.mustWrap) {
      // Both kinds wrap: the phrase at its spaces, the single word inside it.
      const tableNames = seen.names.slice(0, 2);
      expect(tableNames.every((name) => name.lines > 1), JSON.stringify(tableNames)).toBe(true);
    }
    expect(seen.figureLines.every((count) => count === 1), String(seen.figureLines)).toBe(true);
    expect(seen.pills.length).toBe(2);
    for (const pill of seen.pills) expect(pill).toEqual({ inside: true, lines: 1 });

    await expectAccessible(page, `the dashboard with long names, ${size.width}`);
  });
}

// On a phone (D-191): one column; both names wrap inside their cards, nothing sideways.
test("long names wrap inside their cards at 390×844", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await longNamesUser(page);
  await page.goto("/");
  const allocation = page.getByRole("table", { name: "By security" });
  await expect(allocation.getByRole("link", { name: ONE_WORD })).toBeVisible();
  await expect(allocation.getByRole("link", { name: PHRASE })).toBeVisible();

  const seen = await layout(page);
  for (const card of seen.cards) {
    expect(card.scroll, `the card "${card.name}" overflows`).toBeLessThanOrEqual(card.client);
  }
  expect(seen.tableRight).toBeLessThanOrEqual(seen.cardRight + 0.5);
  for (const name of seen.names) {
    expect(name.overflows, `${name.text} overflows its cell`).toBe(false);
    expect(name.lines, `${name.text} wraps`).toBeGreaterThan(1);
  }
  for (const pill of seen.pills) expect(pill).toEqual({ inside: true, lines: 1 });
  const widths = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);
  await expectAccessible(page, "the dashboard with long names, 390");
});

// The Transactions list on a phone (D-192): both names wrap inside their cards, nothing
// sideways, with the table's own box no longer scrolling.
test("long names wrap inside the transaction cards at 390×844", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await longNamesUser(page);
  await page.goto("/transactions");
  const table = page.getByRole("table");
  for (const name of [ONE_WORD, PHRASE]) {
    await expect(table.getByRole("cell", { name, exact: true })).toBeVisible();
  }
  const seen = await table.evaluate((element) =>
    [...element.querySelectorAll<HTMLElement>("tbody tr")].map((row) => ({
      inside: row.scrollWidth <= row.clientWidth,
      right: row.getBoundingClientRect().right <= document.documentElement.clientWidth,
    })),
  );
  expect(seen.length).toBe(3);
  for (const row of seen) expect(row).toEqual({ inside: true, right: true });
  const widths = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);
  await expectAccessible(page, "the transactions with long names, 390");
});

// The import preview on a phone (D-193): a long file name, both names and the notes wrap inside
// their cards, nothing sideways, the lines' own box no longer scrolling.
test("long names wrap inside the import preview's cards at 390×844", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await importInvestor(page, "longnames-import");
  await previewCsv(page, LONG_FILE, LONG_CSV);
  const table = page.getByRole("table");
  for (const name of [ONE_WORD, PHRASE]) {
    await expect(table.getByRole("cell", { name, exact: true }).first()).toBeVisible();
  }
  await expect(page.locator(".import-file-name")).toHaveText(LONG_FILE);
  expect(await cellsOutsideTheirCards(page)).toEqual([]);
  const sections = await page.locator(".import-preview .card").evaluateAll((cards) =>
    cards.filter((card) => card.scrollWidth > card.clientWidth).map((card) => card.textContent?.slice(0, 40)),
  );
  expect(sections, "cards wider than themselves").toEqual([]);
  const widths = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);
  await expectAccessible(page, "the import preview with long names, 390");
});

/** Rows of `table` wider than themselves or than the window, and whether the page scrolls
 * sideways. */
async function rowsOutside(page: Page, table: ReturnType<Page["getByRole"]>) {
  const rows = await table.evaluate((element) =>
    [...element.querySelectorAll<HTMLElement>("tbody tr")]
      .filter((row) => row.scrollWidth > row.clientWidth || row.getBoundingClientRect().right > document.documentElement.clientWidth)
      .map((row) => row.textContent?.slice(0, 40)),
  );
  const sideways = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  return { rows, sideways };
}

// Add holdings on a phone (D-194): each row a card; the hints and the total with both names
// wrap inside it.
test("long names stay inside the add-holdings cards at 390×844", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const assets = await longNamesUser(page);
  await page.goto("/holdings/existing");
  await page.getByLabel("Security, row 1").selectOption(assets[ONE_WORD]);
  await page.getByLabel("Quantity, row 1").fill("5");
  await page.getByLabel("Average cost, row 1").fill("100");
  await page.getByLabel("Security, row 2").selectOption(assets[PHRASE]);
  await page.getByLabel("Security, row 3").selectOption(assets[ONE_WORD]); // "also in row 1"
  await expect(page.getByLabel("Security, row 3")).toHaveAccessibleDescription("The same security is also in row 1.");
  const table = page.getByRole("table", { name: "Securities to add, one per row" });
  expect(await rowsOutside(page, table)).toEqual({ rows: [], sideways: false });
  await expectAccessible(page, "add holdings with long names, 390");
});

// Every manual price at once on a phone (D-194): each holding a card, its name wrapping.
test("long names stay inside the manual prices cards at 390×844", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await longNamesUser(page);
  await page.goto("/holdings/prices");
  const table = page.getByRole("table", { name: "Open holdings whose prices you enter by hand" });
  for (const name of [ONE_WORD, PHRASE]) {
    await expect(table.getByRole("link", { name, exact: true })).toBeVisible();
  }
  expect(await rowsOutside(page, table)).toEqual({ rows: [], sideways: false });
  await expectAccessible(page, "manual prices of all with long names, 390");
});
