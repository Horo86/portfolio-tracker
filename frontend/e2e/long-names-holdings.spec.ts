import { expect, type Page, test } from "./fixtures";

import { expectAccessible, logOut } from "./helpers";
import { holdingsUser, longNamesUser, ONE_WORD, PHRASE } from "./longNames";

// Long security names on the holdings list and the holding detail (Obs 1, D-171), with the two
// invented names of longNames.ts: a single 60-character word and a long phrase. Layout needs a
// real browser, hence this spec.

const SIZES = [
  { width: 1440, height: 900 },
  { width: 1024, height: 768 },
];

async function pageWidths(page: Page) {
  return page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
}

/** The holdings table's own width, and how many lines each name link takes. */
async function holdingsTable(page: Page) {
  await expect(page.getByRole("table").first()).toBeVisible();
  return page.evaluate(() => {
    const table = document.querySelector<HTMLElement>("table.holdings") as HTMLElement;
    const lines = (element: Element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size;
    };
    return {
      width: table.scrollWidth,
      names: [...table.querySelectorAll("tbody th a")].map((link) => ({
        text: link.textContent ?? "",
        lines: lines(link),
      })),
    };
  });
}

for (const size of SIZES) {
  test(`the holdings list keeps long names inside it at ${size.width}×${size.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    // The same holdings with short names: the width a long name must not exceed.
    await holdingsUser(page, ["Short fund A", "Short fund B"]);
    await page.goto("/holdings");
    const ordinary = await holdingsTable(page);
    await logOut(page);

    await longNamesUser(page);
    await page.goto("/holdings");
    await expect(page.getByRole("link", { name: ONE_WORD })).toBeVisible();
    const long = await holdingsTable(page);

    expect(await pageWidths(page)).toEqual(expect.objectContaining({ scroll: size.width }));
    expect(long.width, "a long name widens the table").toBeLessThanOrEqual(ordinary.width + 1);
    expect(long.names.map((name) => name.text).sort()).toEqual([ONE_WORD, PHRASE].sort());
    // Both wrap inside the name column: the phrase at its spaces, the word inside it.
    for (const name of long.names) expect(name.lines, name.text).toBeGreaterThan(1);
    await expectAccessible(page, `the holdings list with long names, ${size.width}`);
  });

  test(`the holding detail keeps a long title inside the page at ${size.width}×${size.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    const assets = await longNamesUser(page);
    for (const name of [ONE_WORD, PHRASE]) {
      await page.goto(`/holdings/${assets[name]}`);
      const title = page.getByRole("heading", { level: 1, name });
      await expect(title).toBeVisible();

      const widths = await pageWidths(page);
      expect(widths.scroll, `the page scrolls sideways (${name})`).toBe(widths.client);
      const box = await title.boundingBox();
      expect(box && box.x + box.width).toBeLessThanOrEqual(widths.client);
      for (const label of ["Add transaction", "Manual prices", "Edit security"]) {
        const button = await page.getByRole("link", { name: label }).or(
          page.getByRole("button", { name: label }),
        ).first().boundingBox();
        expect(button, label).not.toBeNull();
        expect((button?.x ?? 0) + (button?.width ?? 0), `${label} off screen`).toBeLessThanOrEqual(
          widths.client,
        );
      }
      const cards = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>("main .card")].map((card) => ({
          name: card.querySelector("h2")?.textContent ?? card.className,
          scroll: card.scrollWidth,
          client: card.clientWidth,
        })),
      );
      for (const card of cards) {
        expect(card.scroll, `the card "${card.name}" overflows (${name})`).toBeLessThanOrEqual(
          card.client + 1,
        );
      }
      await expectAccessible(page, `the holding detail of a long name, ${size.width}`);
    }
  });
}
