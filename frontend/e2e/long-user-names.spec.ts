import { expect, type Page, test } from "./fixtures";

import { dashboardHeading, logIn, newUser } from "./helpers";

// Long first and last names and a long e-mail (D-173): invented, at the limits the API allows.
// The greeting, the sidebar and the Users screen must wrap them, never overflow. Layout needs a
// real browser, hence this spec.

/** One word of 100 letters: wraps only thanks to overflow-wrap: anywhere. */
const FIRST = "Lunghissimonome".repeat(7).slice(0, 100);
/** A long last name with spaces and an apostrophe: wraps at the spaces. */
const LAST = "De La Esempio Di Prova Con Un Cognome Molto Lungo D'Altri Tempi E Altre Parole Ancora";
/** A long e-mail, one per test (e-mails are unique): a 64-character local part and a long
 * domain, no space to break at. */
const emailFor = (width: number) =>
  `${String(width).padEnd(64, "a")}@${"sottodominio-molto-lungo.".repeat(5)}example.org`;

test.beforeAll(() => {
  expect([...FIRST].length).toBe(100);
  expect(FIRST).not.toContain(" ");
  expect([...LAST].length).toBeLessThanOrEqual(100);
  expect(emailFor(1440).length).toBeLessThanOrEqual(254);
});

type Box = { what: string; scroll: number; client: number; right: number; limit: number };

/** What would show a horizontal overflow: the page itself and the boxes holding the names. */
async function overflows(page: Page, selectors: Record<string, [string, string]>) {
  return page.evaluate((pairs) => {
    const boxes: Box[] = [];
    for (const [what, [inner, outer]] of Object.entries(pairs)) {
      const element = document.querySelector<HTMLElement>(inner);
      const container = document.querySelector<HTMLElement>(outer);
      if (!element || !container) throw new Error(`${what}: ${inner} or ${outer} not found`);
      boxes.push({
        what,
        scroll: element.scrollWidth,
        client: element.clientWidth,
        right: element.getBoundingClientRect().right,
        limit: container.getBoundingClientRect().right,
      });
    }
    const page = document.documentElement;
    return {
      page: { scroll: page.scrollWidth, client: page.clientWidth },
      boxes: boxes.filter((box) => box.scroll > box.client + 1 || box.right > box.limit + 0.5),
    };
  }, selectors);
}

for (const size of [
  { width: 1440, height: 900 },
  { width: 1024, height: 768 },
]) {
  test(`long names and e-mail wrap at ${size.width}×${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    const names = { first_name: FIRST, last_name: LAST };
    const email = emailFor(size.width);
    const long = await newUser("longuser", { names, email, role: "ADMINISTRATOR" });

    // The greeting and the sidebar of the user with the long names.
    await logIn(page, long);
    await expect(page.getByRole("heading", { name: dashboardHeading(long), level: 1 })).toBeVisible();
    await expect(page.locator(".user-name")).toHaveText(`${FIRST} ${LAST}`);
    const own = await overflows(page, {
      greeting: ["main h1", "main"],
      sidebar: [".user-name", ".user-box"],
      userBox: [".user-box", ".sidebar"],
    });
    expect(own.boxes).toEqual([]);
    expect(own.page.scroll).toBeLessThanOrEqual(own.page.client);

    // The Users screen, as that same administrator: their own row has the long values.
    await page.getByRole("link", { name: "Administration" }).click();
    const row = page.getByRole("row").filter({ has: page.getByRole("rowheader", { name: long.username }) });
    await expect(row.getByText(email)).toBeVisible();
    const users = await overflows(page, {
      table: [".users-table", ".card"],
      scroller: [".table-scroll", ".card"],
    });
    expect(users.boxes).toEqual([]);
    const cells = await row.evaluate((tr) =>
      [...tr.querySelectorAll<HTMLElement>("th, td")]
        .filter((cell) => cell.scrollWidth > cell.clientWidth + 1)
        .map((cell) => cell.className || cell.tagName),
    );
    expect(cells).toEqual([]); // the name and the e-mail wrap inside their own cells
    expect(users.page.scroll).toBeLessThanOrEqual(users.page.client);
  });
}
