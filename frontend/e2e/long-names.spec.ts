import { expect, type Page, test } from "./fixtures";

import { expectAccessible } from "./helpers";
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
