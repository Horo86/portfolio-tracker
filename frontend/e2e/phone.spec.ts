import type { Page } from "@playwright/test";

import { MARKET } from "../playwright.config";
import { addInvestor, cardBoxes, deleteElsewhere, fullWidthActions, outsideTheWindow } from "./addPhone";
import {
  administrationInTheMenu,
  focusRingOnTheBar,
  walkTheShell,
  wideContentStaysInside,
} from "./compactShell";
import { expect, test } from "./fixtures";
import {
  apiPost,
  CSRF,
  expectAccessible,
  expectBarInView,
  expectNoSidewaysScroll,
  expectTouchTargets,
  logIn,
  newUser,
} from "./helpers";
import {
  cellsOutsideTheirCards,
  chooseMadeFile,
  confirmBarAt,
  importInvestor,
  LONG_CSV,
  LONG_FILE,
  previewCsv,
  previewDirecta,
  TWO_ACCOUNTS_CSV,
} from "./importPhone";
import { ONE_WORD, PHRASE } from "./longNames";
import { marketInvestor } from "./marketUsers";

// Invented ISINs the fake market server knows (backend/tests/e2e_market_server.py): a stock
// listed in Milan in EUR, and a code no provider knows.
const MARKET_STOCK_ISIN = "ZZ0000087021"; // fake_isin(8702)
const MARKET_BOND_ISIN = "ZZ0000087039"; // fake_isin(8703)

// A phone (D-189): 390x844 with touch, in Chromium (no Playwright project of its own, B12).
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

test("phone: the bar, the menu, the account in view and log out", async ({ page }) => {
  await walkTheShell(page, "phone");
});

test("phone: an administrator reaches Administration from the menu", async ({ page }) => {
  await administrationInTheMenu(page, "phone-admin");
});

test("phone: the bar's entries show the keyboard focus", async ({ page }) => {
  await focusRingOnTheBar(page, "phone-focus");
});

test("phone: wide pages stay inside the window and the bar stays on screen", async ({ page }) => {
  await wideContentStaysInside(page, ["/", "/transactions", "/settings", "/admin"]);
});

test.describe("at 320 px", () => {
  test.use({ viewport: { width: 320, height: 640 } });

  test("phone: the dashboard stays inside the window and the bar stays on screen", async ({ page }) => {
    await wideContentStaysInside(page, ["/"]);
  });

  test("phone: Administration, with its price sources, stays inside the window", async ({ page }) => {
    await openAdministration(page, "phone-320");
    const sources = page.getByRole("region", { name: "Price sources" });
    await sources.scrollIntoViewIfNeeded();
    await expect(sources.getByRole("term").first()).toBeVisible();
    // Each role, then its engines under it, from the card's left edge.
    const rows = await sources.locator(".row").evaluateAll((list) =>
      list.map((row) => {
        const term = row.querySelector("dt")!.getBoundingClientRect();
        const value = row.querySelector("dd")!.getBoundingClientRect();
        return { under: value.top >= term.bottom, left: Math.abs(value.left - term.left) < 1 };
      }),
    );
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) expect(row).toEqual({ under: true, left: true });
    await expectNoSidewaysScroll(page);
    await expectBarInView(page);
  });
});

// --- Responsive components (D-190): tables as cards, dialogs full screen ------------------

/** A new administrator, with an investor beside it in the list, on Administration. */
async function openAdministration(page: Page, prefix: string) {
  await newUser(`${prefix}-inv`); // a row with actions
  const admin = await newUser(prefix, { role: "ADMINISTRATOR" });
  await logIn(page, admin);
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Administration", level: 1 })).toBeVisible();
  await expect(page.getByRole("table", { name: "Users" })).toBeVisible();
  return admin;
}

/** The dialog covers the window, and its buttons take the whole width of its content. */
async function expectFullScreen(page: Page, name: string) {
  const dialog = page.getByRole("dialog", { name });
  await expect(dialog).toBeVisible();
  const box = await dialog.boundingBox();
  const size = page.viewportSize()!;
  expect(box).toEqual({ x: 0, y: 0, width: size.width, height: size.height });
  const buttons = await dialog.locator(".actions .button").evaluateAll((list) =>
    list.map((button) => {
      const own = button.getBoundingClientRect();
      const row = button.parentElement!.getBoundingClientRect();
      return Math.round(own.width) === Math.round(row.width);
    }),
  );
  expect(buttons.length).toBeGreaterThan(0);
  expect(buttons.every(Boolean), "every button full width").toBe(true);
  return dialog;
}

test("phone: Administration's users are cards, still a table for screen readers", async ({ page }) => {
  const admin = await openAdministration(page, "phone-cards");
  const table = page.getByRole("table", { name: "Users" });
  // Roles and names as on a desktop: headers, rows, a row header and cells.
  await expect(table.getByRole("columnheader")).toHaveCount(7);
  const own = table.getByRole("row").filter({ has: page.getByRole("rowheader", { name: admin.username }) });
  await expect(own.getByRole("cell", { name: "Administrator" })).toBeVisible();
  await expect(own.getByRole("cell", { name: `${admin.username}@example.org` })).toBeVisible();

  const layout = await own.evaluate((row) => {
    const box = (el: Element) => el.getBoundingClientRect();
    const cells = [...row.children];
    const header = row.closest("table")!.querySelector("thead th")!;
    return {
      display: getComputedStyle(row).display,
      headerHidden: box(header).width <= 1 && box(header).height <= 1,
      label: getComputedStyle(cells[1], "::before").display,
      rowWidth: Math.round(box(row).width),
      email: Math.round(box(cells[1]).width),
      // "You" beside the name; role and status, 2FA and last access side by side.
      youBeside: Math.abs(box(cells[6]).top - box(cells[0]).top) < 2 && box(cells[6]).left > box(cells[0]).left,
      pairs: [
        [2, 3],
        [4, 5],
      ].every(([a, b]) => Math.abs(box(cells[a]).top - box(cells[b]).top) < 2 && box(cells[b]).left > box(cells[a]).right),
    };
  });
  expect(layout).toEqual({
    display: "grid",
    headerHidden: true,
    label: "block",
    rowWidth: layout.rowWidth,
    email: layout.rowWidth,
    youBeside: true,
    pairs: true,
  });
  await expectTouchTargets(page, ".users-table tbody");
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "Administration's users as cards");
});

test("phone: the new user dialog fills the screen and gives the focus back", async ({ page }) => {
  await openAdministration(page, "phone-dialog");
  const opener = page.getByRole("button", { name: "New user" });
  await opener.click();
  const dialog = await expectFullScreen(page, "New user");
  await expectTouchTargets(page, "dialog");
  await expectAccessible(page, "the new user dialog on a phone");
  // The buttons come at the end of the content: the last field is above them.
  const cancel = dialog.getByRole("button", { name: "Cancel" });
  const role = await dialog.getByLabel("Role").boundingBox();
  expect((await cancel.boundingBox())!.y).toBeGreaterThan(role!.y + role!.height);
  await cancel.click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test("phone: the Security card's errors and its confirmation", async ({ page }) => {
  await openAdministration(page, "phone-security");
  const card = page.getByRole("region", { name: "Security" });
  const idle = card.getByLabel("Days without use");
  const save = card.getByRole("button", { name: "Save" });
  const before = await idle.inputValue();
  await idle.fill("31");
  await save.click();
  await expect(idle).toBeFocused();
  await expect(card.locator(".field-error")).toBeVisible();
  await expectNoSidewaysScroll(page);
  const width = async (locator: typeof save) => Math.round((await locator.boundingBox())!.width);
  expect(await width(save)).toBe(await width(card.locator(".actions")));

  await idle.fill(before === "2" ? "3" : "2"); // a valid change, never saved here
  await save.click();
  const dialog = await expectFullScreen(page, "Save the security settings?");
  await expectAccessible(page, "the security confirmation on a phone");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(save).toBeFocused();
});

// --- Dashboard, holdings and a holding's page (D-191) -------------------------------------

type Investor = { stock: string; bond: string; account: string };

/** A new investor with two accounts: a share bought, partly sold and paying a dividend, a
 * bond with a manual price, and a fund in the second account. All invented. */
async function phoneInvestor(page: Page, prefix: string): Promise<Investor> {
  const user = await newUser(prefix);
  await logIn(page, user);
  const post = <T,>(path: string, data: unknown) => apiPost<T>(page, path, data);
  const eur = await post<{ id: string }>("/accounts", { name: "Phone account", currency: "EUR" });
  const usd = await post<{ id: string }>("/accounts", { name: "Second account", currency: "USD" });
  for (const [account, amount] of [[eur.id, "20000.00"], [usd.id, "5000.00"]]) {
    await post("/transactions", {
      account_id: account, type: "DEPOSIT", trade_date: "2025-01-02", total_amount: amount,
    }); // prettier-ignore
  }
  const asset = async (name: string, asset_type: string, currency: string, unit = "CURRENCY") =>
    (await post<{ id: string }>("/assets", {
      name, asset_type, quote_currency: currency, quote_unit: unit,
    })).id; // prettier-ignore
  const stock = await asset("Phone Example Industries", "STOCK", "EUR");
  const bond = await asset("Phone Example Bond 2031", "BOND", "EUR", "PERCENT_OF_NOMINAL");
  const fund = await asset("Phone Example Fund", "ETF", "USD");
  // A dividend inside the last 12 months, so the dashboard draws its month bars.
  const recent = new Date(Date.now() - 40 * 86_400_000).toISOString().slice(0, 10);
  const rows: [string, string, string, string, Record<string, string>][] = [
    [eur.id, stock, "BUY", "2025-02-03", { quantity: "100", unit_price: "12.00",
      gross_amount: "1200.00", fees: "2.00", total_amount: "1202.00" }],
    [eur.id, stock, "SELL", "2025-06-12", { quantity: "40", unit_price: "14.00",
      gross_amount: "560.00", fees: "2.00", taxes: "20.07", total_amount: "537.93" }],
    [eur.id, stock, "DIVIDEND", recent, { gross_amount: "30.00", taxes: "7.80",
      total_amount: "22.20" }],
    [eur.id, bond, "BUY", "2025-02-17", { quantity: "5000", unit_price: "98.00",
      gross_amount: "4900.00", fees: "3.00", total_amount: "4903.00" }],
    [usd.id, fund, "BUY", "2025-03-05", { quantity: "20", unit_price: "150.00",
      gross_amount: "3000.00", fees: "1.00", total_amount: "3001.00" }],
  ]; // prettier-ignore
  for (const [account, asset_id, type, trade_date, fields] of rows) {
    await post("/transactions", { account_id: account, asset_id, type, trade_date, ...fields });
  }
  for (const [id, close] of [[stock, "13.25"], [bond, "99.15"], [fund, "162.50"]]) {
    const priced = await page.request.put(`/api/v1/assets/${id}/prices`, {
      data: { day: "2025-12-30", close },
      headers: CSRF,
    });
    expect(priced.ok(), "manual price").toBe(true);
  }
  return { stock, bond, account: eur.id };
}

/** The boxes of a row's cells, to check which ones share a line. */
async function cellBoxes(row: ReturnType<Page["locator"]>) {
  return row.evaluate((tr) =>
    [...tr.children].map((cell) => {
      const box = cell.getBoundingClientRect();
      return { top: Math.round(box.top), left: Math.round(box.left), width: Math.round(box.width) };
    }),
  );
}

test("phone: the dashboard is one column, its charts drawn, nothing sideways", async ({ page }) => {
  await phoneInvestor(page, "phone-dash");
  await page.goto("/");
  await expect(page.getByRole("table", { name: "By type" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  // The donut, the currency bar and the month bars, each drawn (an SVG).
  for (const chart of [".chart-donut", ".chart-bar", ".chart-months"]) {
    await expect(page.locator(`${chart} svg`).first(), chart).toBeVisible();
  }
  // The figure cards stacked; the gains table still a table.
  const lefts = await page
    .locator(".dashboard-cards > .card, .dashboard-columns > .card")
    .evaluateAll((cards) => cards.map((card) => Math.round(card.getBoundingClientRect().left)));
  expect(new Set(lefts).size, "one column").toBe(1);
  const gains = page.locator(".gain-table");
  expect(await gains.locator("tbody tr").first().evaluate((tr) => getComputedStyle(tr).display)).toBe(
    "table-row",
  );
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "the dashboard on a phone");
  await expectBarInView(page);
});

test("phone: the holdings are cards, and a tap anywhere on one opens the holding", async ({ page }) => {
  const { stock } = await phoneInvestor(page, "phone-holdings");
  await page.goto("/holdings");
  const table = page.getByRole("table", { name: /securities/ });
  await expect(table).toBeVisible();
  // Still a table for screen readers: its headers and a row header per security.
  await expect(table.getByRole("columnheader")).toHaveCount(8);
  const row = table.getByRole("row").filter({
    has: page.getByRole("rowheader", { name: /Phone Example Industries/ }),
  });
  const cells = await cellBoxes(row);
  const width = cells[0].width;
  // Name and accounts wide; then quantity | cost, price | value, gross | net side by side.
  expect(cells[1].width).toBe(width);
  for (const [a, b] of [[2, 3], [4, 5], [6, 7]]) {
    expect(cells[a].top, `cells ${a} and ${b} on one line`).toBe(cells[b].top);
    expect(cells[b].left).toBeGreaterThan(cells[a].left + cells[a].width - 1);
  }
  // The head's buttons take the whole width, one under another.
  const head = await page.locator(".page-head .head-actions > *").evaluateAll((list) =>
    list.map((el) => [Math.round(el.getBoundingClientRect().width), el.parentElement!.clientWidth]),
  );
  expect(head.length).toBe(3);
  for (const [own, all] of head) expect(own).toBe(all);
  await expectTouchTargets(page, ".page-head, .filters .field");
  // A tick with its label: the label, 44 px high, is the target (D-190 (4)).
  const ticks = await page.locator(".filters .check").evaluateAll((list) =>
    list.map((label) => label.getBoundingClientRect().height >= 44),
  );
  expect(ticks).toEqual([true, true]);
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "the holdings as cards");

  // A tap on the value, away from the name, opens the holding.
  const value = row.getByRole("cell").nth(3);
  await value.scrollIntoViewIfNeeded();
  const box = (await value.boundingBox())!;
  await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
  await expect(page).toHaveURL(new RegExp(`/holdings/${stock}`));
  await expect(page.getByRole("heading", { level: 1, name: "Phone Example Industries" })).toBeVisible();
});

test("phone: the keyboard focus on a holding is drawn around the whole card", async ({ page }) => {
  await phoneInvestor(page, "phone-holdings-focus");
  await page.goto("/holdings");
  await expect(page.getByRole("table", { name: /securities/ })).toBeVisible();
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(() => document.activeElement?.classList.contains("holding-link"))) break;
  }
  const ring = await page.evaluate(() => {
    const link = document.activeElement as HTMLElement;
    const row = link.closest("tr")!;
    const style = getComputedStyle(row);
    const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
    const probe = document.createElement("span");
    probe.style.color = accent; // a probe element only, never in the page's own markup
    document.body.append(probe);
    const accentColour = getComputedStyle(probe).color;
    probe.remove();
    return {
      link: link.classList.contains("holding-link"),
      visible: link.matches(":focus-visible"),
      outline: `${style.outlineStyle} ${style.outlineWidth}`,
      accent: style.outlineColor === accentColour,
      // The ring is the card's: as wide as the row, much wider than the name.
      wholeCard: row.getBoundingClientRect().width > link.getBoundingClientRect().width,
    };
  });
  expect(ring).toEqual({
    link: true,
    visible: true,
    outline: "solid 3px",
    accent: true,
    wholeCard: true,
  });
});

test("phone: a holding's page, its buttons and its transactions as cards", async ({ page }) => {
  const { stock, account } = await phoneInvestor(page, "phone-detail");
  await page.goto(`/holdings/${stock}?account=${account}`);
  await expect(page.getByRole("heading", { level: 1, name: "Phone Example Industries" })).toBeVisible();
  // Buttons two per row: "Add transaction" beside "Manual prices", "Edit security" alone.
  const buttons = await page.locator(".holding-head .head-actions > *").evaluateAll((list) =>
    list.map((el) => {
      const box = el.getBoundingClientRect();
      return { top: Math.round(box.top), width: Math.round(box.width) };
    }),
  );
  expect(buttons.length).toBe(3);
  expect(buttons[0].top).toBe(buttons[1].top);
  expect(buttons[2].top).toBeGreaterThan(buttons[0].top);
  expect(buttons[2].width).toBeGreaterThan(buttons[0].width + buttons[1].width);

  const transactions = page.getByRole("region", { name: "Transactions of this security" });
  const table = transactions.getByRole("table");
  await expect(table.getByRole("row")).toHaveCount(4); // the header row and three
  const sale = table.getByRole("row").filter({ hasText: "Sell" });
  const cells = await cellBoxes(sale);
  // Date | type, quantity | price, amount | fees, taxes | cash, then "Edit" (one account in view).
  for (const [a, b] of [[0, 1], [2, 3], [4, 5], [6, 7]]) {
    expect(cells[a].top, `cells ${a} and ${b} on one line`).toBe(cells[b].top);
  }
  expect(cells[8].top).toBeGreaterThan(cells[7].top);
  await expectTouchTargets(page, ".holding-transactions tbody, .holding-head, .selection-bar");
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "a holding's page on a phone");

  // "Delete all" stays reachable; its dialog fills the screen; nothing is deleted here.
  await transactions.getByRole("button", { name: "Delete all transactions of this security…" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  expect(await dialog.boundingBox()).toEqual(
    expect.objectContaining({ x: 0, y: 0, width: 390 }),
  );
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // "Edit" on a card opens the transaction.
  await sale.getByRole("link", { name: /^Edit: Sell/ }).click();
  await expect(page).toHaveURL(/\/transactions\/.+\/edit/);
});

test.describe("dashboard, holdings and a holding's page at 320 px", () => {
  test.use({ viewport: { width: 320, height: 640 } });

  test("phone: nothing scrolls sideways and the bar stays on screen", async ({ page }) => {
    const { bond } = await phoneInvestor(page, "phone-320-read");
    for (const [path, heading] of [
      ["/", null],
      ["/holdings", "Holdings"],
      [`/holdings/${bond}`, "Phone Example Bond 2031"],
    ] as const) {
      await page.goto(path);
      const h1 = page.getByRole("heading", { level: 1 });
      await expect(heading ? h1.filter({ hasText: heading }) : h1).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expectNoSidewaysScroll(page);
      await expectBarInView(page);
    }
  });
});

// --- Transactions (D-192) -----------------------------------------------------------------

/** The phone investor, with six more deposits so the list is several screens long. */
async function transactionsInvestor(page: Page, prefix: string) {
  const investor = await phoneInvestor(page, prefix);
  for (let n = 1; n <= 6; n++) {
    await apiPost(page, "/transactions", {
      account_id: investor.account, type: "DEPOSIT", trade_date: `2024-0${n}-10`, total_amount: `${n}00.00`,
    }); // prettier-ignore
  }
  await page.goto("/transactions");
  await expect(page.getByRole("heading", { name: "Transactions", level: 1 })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  return investor;
}

const SELECT_ALL = "Select every row of this page";

test("phone: the transactions are cards, still a table for screen readers", async ({ page }) => {
  await transactionsInvestor(page, "phone-tx-cards");
  const table = page.getByRole("table");
  await expect(table.getByRole("columnheader")).toHaveCount(9); // the account column too
  const sale = table.getByRole("row").filter({ hasText: "Sell" });
  await expect(sale.getByRole("cell", { name: "Phone Example Industries", exact: true })).toBeVisible();
  const cells = await cellBoxes(sale);
  // Tick | date | type on one line; the security and the account wide; quantity | amount,
  // cash effect | "Edit", the right column under the type.
  for (const [a, b] of [[0, 1], [1, 2], [5, 6]]) {
    expect(Math.abs(cells[a].top - cells[b].top), `cells ${a} and ${b} on one line`).toBeLessThan(16);
  }
  expect(cells[3].width).toBe(cells[4].width);
  expect(cells[3].top).toBeGreaterThan(cells[0].top);
  expect(cells[4].top).toBeGreaterThan(cells[3].top);
  expect(cells[5].top).toBeGreaterThan(cells[4].top);
  expect(cells[7].top).toBeGreaterThan(cells[5].top);
  for (const n of [6, 8]) expect(cells[n].left, `cell ${n} in the right column`).toBe(cells[2].left);
  expect(cells[5].left).toBe(cells[7].left);

  // The head: "Add transaction" alone, Import and Export side by side.
  const head = await page.locator(".page-head .head-actions > *").evaluateAll((list) =>
    list.map((el) => {
      const box = el.getBoundingClientRect();
      return { top: Math.round(box.top), width: Math.round(box.width) };
    }),
  );
  expect(head.length).toBe(3);
  expect(head[1].top).toBe(head[2].top);
  expect(head[1].top).toBeGreaterThan(head[0].top);
  expect(head[0].width).toBeGreaterThan(head[1].width + head[2].width);

  // 44 px: the head, the filters, the bar, the pager, "Edit"; each tick's label.
  await expectTouchTargets(page, ".page-head, .filters select, .tx-selection-bar, .pager, .tx-edit");
  const ticks = await page.locator(".tx-table label.tick").evaluateAll((list) =>
    list.map((label) => {
      const box = label.getBoundingClientRect();
      return box.width >= 44 && box.height >= 44;
    }),
  );
  expect(ticks.length).toBe(14); // select all and thirteen rows
  expect(ticks.every(Boolean), "every tick 44 px").toBe(true);
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "the transactions as cards");
  await expectBarInView(page);
});

test("phone: one account in view has no account line", async ({ page }) => {
  const { account } = await transactionsInvestor(page, "phone-tx-one");
  await page.goto(`/transactions?account=${account}`);
  const table = page.getByRole("table");
  await expect(table.getByRole("columnheader")).toHaveCount(8);
  const deposit = table.getByRole("row").filter({ hasText: "€600.00" });
  const cells = await cellBoxes(deposit);
  expect(cells.length).toBe(8);
  // The security's line ("—" for a deposit), then quantity | amount.
  expect(cells[4].top).toBe(cells[5].top);
  expect(cells[4].top).toBeGreaterThan(cells[3].top);
});

test("phone: select all, with the text beside its box", async ({ page }) => {
  await transactionsInvestor(page, "phone-tx-all");
  const all = page.getByRole("checkbox", { name: SELECT_ALL });
  const label = page.locator(".tx-table thead label.tick");
  await expect(label).toHaveText("Select every row");
  await expect(label.locator(".tick-text")).toBeVisible();
  await label.locator(".tick-text").click();
  await expect(all).toBeChecked();
  await expect(page.getByText("13 selected", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: /^Select: Sell/ }).uncheck();
  await expect(all).not.toBeChecked();
  expect(await all.evaluate((box: HTMLInputElement) => box.indeterminate)).toBe(true);
  await expect(page.getByText("12 selected", { exact: true })).toBeVisible();
  await expectAccessible(page, "the transactions with some ticked");
});

test("phone: the selection bar stays at the top while scrolling", async ({ page }) => {
  await transactionsInvestor(page, "phone-tx-sticky");
  const bar = page.locator(".tx-selection-bar");
  /** Where the bar is, after scrolling the window to `y`, and what covers its middle. */
  const at = (y: number) =>
    page.evaluate((top) => {
      window.scrollTo(0, top);
      const el = document.querySelector<HTMLElement>(".tx-selection-bar")!;
      const box = el.getBoundingClientRect();
      const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      return {
        scrolled: window.scrollY > 0,
        top: Math.round(box.top),
        position: getComputedStyle(el).position,
        onTop: el.contains(hit),
      };
    }, y);
  // Nothing selected: in its place, it scrolls away with the list.
  const idle = await at(1200);
  expect(idle.position).toBe("static");
  expect(idle.top).toBeLessThan(0);

  await page.evaluate(() => window.scrollTo(0, 0));
  await page.getByRole("checkbox", { name: /^Select: Sell/ }).check();
  await page.getByRole("checkbox", { name: /^Select: Deposit/ }).first().check();
  await expect(bar).toContainText("2 selected");
  for (const y of [900, 1600]) {
    const seen = await at(y);
    expect(seen, `scrolled to ${y}`).toEqual({ scrolled: true, top: 0, position: "sticky", onTop: true });
  }
  // Its two buttons side by side, 44 px; the bottom bar still on screen and reachable.
  const buttons = await bar.locator(".actions .button").evaluateAll((list) =>
    list.map((el) => Math.round(el.getBoundingClientRect().top)),
  );
  expect(buttons[0]).toBe(buttons[1]);
  await expectTouchTargets(page, ".tx-selection-bar");
  const bottom = await page.evaluate(() => {
    const nav = document.querySelector<HTMLElement>(".bottom-nav")!.getBoundingClientRect();
    const hit = document.elementFromPoint(nav.left + nav.width / 2, nav.top + nav.height / 2);
    return nav.bottom <= window.innerHeight && !!hit?.closest(".bottom-nav");
  });
  expect(bottom, "the bottom bar in view").toBe(true);

  // Bulk edit and bulk delete fill the screen and close with Cancel; nothing changes.
  await bar.getByRole("button", { name: "Edit selected" }).click();
  const edit = await expectFullScreen(page, "Edit 2 transactions together");
  await expectAccessible(page, "bulk edit on a phone");
  await edit.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await bar.getByRole("button", { name: "Delete selected" }).click();
  const remove = await expectFullScreen(page, "Delete 2 transactions?");
  await remove.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(bar).toContainText("2 selected");
});

test("phone: a purchase through the form, edited, then deleted", async ({ page }) => {
  await transactionsInvestor(page, "phone-tx-form");
  await page.getByRole("link", { name: "Add transaction" }).click();
  await expect(page.getByRole("heading", { name: "New transaction", level: 1 })).toBeFocused();
  await page.getByLabel("Account", { exact: true }).selectOption({ label: "Phone account (EUR)" });
  await page.getByLabel("Security").selectOption({ label: "Phone Example Industries · EUR" });
  await page.getByLabel("Date", { exact: true }).fill("03/11/2025");
  await page.getByLabel("Quantity").fill("10");
  await page.getByLabel("Unit price (EUR)").fill("13.50");
  await page.getByLabel("Fees (EUR)").fill("1.50");
  const summary = page.getByRole("complementary", { name: "Summary" });
  await expect(summary).toContainText("Total cost€136.50");

  const form = await page.evaluate(() => {
    const box = (el: Element) => el.getBoundingClientRect();
    const fields = [...document.querySelectorAll<HTMLInputElement>("main input, main select, main textarea")]
      .filter((el) => el.offsetParent !== null && el.type !== "radio" && el.type !== "checkbox");
    const details = document.querySelector("#tx-details")!.closest("section")!;
    const aside = document.querySelector("aside.summary")!;
    const date = document.querySelector(".date-input")!;
    const calendar = date.querySelector(".date-input-calendar")!;
    const pills = [...document.querySelectorAll(".type-option")].map((el) => box(el).right);
    return {
      // No field zooms the page on focus.
      small: fields.filter((el) => parseFloat(getComputedStyle(el).fontSize) < 16).map((el) => el.id),
      decimal: document.querySelector<HTMLInputElement>("input[inputmode='decimal']") !== null,
      summaryBelow: box(aside).top >= box(details).bottom,
      summaryPosition: getComputedStyle(aside).position,
      date: Math.round(box(date).height),
      calendar: [Math.round(box(calendar).width), Math.round(box(calendar).height)],
      pillsInside: pills.every((right) => right <= document.documentElement.clientWidth),
    };
  });
  expect(form).toEqual({
    small: [],
    decimal: true,
    summaryBelow: true,
    summaryPosition: "static",
    date: 44,
    calendar: [44, form.calendar[1]],
    pillsInside: true,
  });
  expect(form.calendar[1]).toBeGreaterThanOrEqual(42); // inside the field's 1 px border
  await expectTouchTargets(page, ".tx-actions, .type-choice");
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "the transaction form on a phone");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Transaction saved." })).toBeVisible();

  // The edit page: the buttons full width, "Delete…" last on screen.
  await page.getByRole("link", { name: /^Edit: Buy, Phone Example Industries, Nov 3, 2025/ }).click();
  await expect(page.getByRole("heading", { name: "Edit transaction", level: 1 })).toBeVisible();
  await expect(page.getByRole("button", { name: "Delete…" })).toBeVisible();
  const actions = await page.locator(".tx-actions .actions > *").evaluateAll((list) =>
    list.map((el) => {
      const box = el.getBoundingClientRect();
      return { text: el.textContent, top: box.top, full: Math.round(box.width) === el.parentElement!.clientWidth };
    }),
  );
  expect(actions.map((a) => a.full)).toEqual([true, true, true]);
  const byTop = [...actions].sort((a, b) => a.top - b.top).map((a) => a.text);
  expect(byTop).toEqual(["Cancel", "Save", "Delete…"]);
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "the edit page on a phone");

  await page.getByRole("button", { name: "Delete…" }).click();
  const dialog = await expectFullScreen(page, "Delete this transaction?");
  await expect(dialog).toContainText("After the deletion:");
  await expectAccessible(page, "the delete dialog on a phone");
  await dialog.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Transaction deleted." })).toBeVisible();
  await expect(page).toHaveURL(/\/transactions/);
});

test("phone: selling more than is held opens the conflict dialog, full screen", async ({ page }) => {
  await transactionsInvestor(page, "phone-tx-conflict");
  await page.getByRole("link", { name: "Add transaction" }).click();
  await page.getByRole("radio", { name: "Sell" }).check();
  await page.getByLabel("Account", { exact: true }).selectOption({ label: "Phone account (EUR)" });
  await page.getByLabel("Security").selectOption({ label: "Phone Example Industries · EUR" });
  await page.getByLabel("Date", { exact: true }).fill("03/11/2025");
  await page.getByLabel("Quantity").fill("1000");
  await page.getByLabel("Unit price (EUR)").fill("13");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = await expectFullScreen(page, "This change cannot be saved");
  await expectAccessible(page, "the conflict dialog on a phone");
  await dialog.getByRole("button", { name: "Back to the form" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeFocused();
});

test.describe("transactions at 320 px", () => {
  test.use({ viewport: { width: 320, height: 640 } });

  test("phone: long names on the list, the form and the edit page stay inside", async ({ page }) => {
    const user = await newUser("phone-320-tx");
    await logIn(page, user);
    // Invented: one long word for the security and the account, a long note.
    const word = "FixtureWorldwideDevelopedMarketsEquityIndexAccumulatingTrust";
    const account = await apiPost<{ id: string }>(page, "/accounts", {
      name: "LongAccountNameWithoutAnySpaceForTheNarrowestPhoneScreens", currency: "EUR",
    }); // prettier-ignore
    await apiPost(page, "/accounts", { name: "Other account", currency: "EUR" });
    const asset = await apiPost<{ id: string }>(page, "/assets", {
      name: word, asset_type: "ETF", quote_currency: "EUR", quote_unit: "CURRENCY",
    }); // prettier-ignore
    await apiPost(page, "/transactions", {
      account_id: account.id, type: "DEPOSIT", trade_date: "2025-01-02", total_amount: "123456789.00",
    }); // prettier-ignore
    await apiPost(page, "/transactions", {
      account_id: account.id, type: "BUY", trade_date: "2025-01-10", asset_id: asset.id,
      quantity: "1234567.12345678", unit_price: "100.00", gross_amount: "123456712.35", fees: "0.00",
      total_amount: "123456712.35",
      notes: `${word}${word} a long note that the edit page shows in its field`,
    }); // prettier-ignore
    await page.goto("/transactions");
    await expect(page.getByRole("cell", { name: word, exact: true })).toBeVisible();
    await page.getByRole("checkbox", { name: SELECT_ALL }).check();
    await page.waitForLoadState("networkidle");
    await expectNoSidewaysScroll(page);
    await expectBarInView(page);

    await page.getByRole("link", { name: new RegExp(`^Edit: Buy, ${word}`) }).click();
    await expect(page.getByRole("heading", { name: "Edit transaction", level: 1 })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Summary" })).toContainText("Quantity after");
    await expectNoSidewaysScroll(page);
    await expectBarInView(page);

    await page.goto("/transactions/new");
    await expect(page.getByRole("heading", { name: "New transaction", level: 1 })).toBeVisible();
    await page.getByLabel("Security").selectOption({ label: `${word} · EUR` });
    await expectNoSidewaysScroll(page);
    await expectBarInView(page);
  });
});

// --- Import (D-193) ----------------------------------------------------------------------

/** The middle, top, bottom, left and width of each cell of a line's card. */
async function cellPlaces(row: ReturnType<Page["locator"]>) {
  return row.evaluate((tr) =>
    [...tr.children].map((cell) => {
      const box = cell.getBoundingClientRect();
      return {
        middle: Math.round(box.top + box.height / 2),
        top: Math.round(box.top),
        bottom: Math.round(box.bottom),
        left: Math.round(box.left),
        width: Math.round(box.width),
      };
    }),
  );
}

test("phone: the Directa fixture imported end to end, its lines as cards", async ({ page }) => {
  await importInvestor(page, "phone-import");
  await page.goto("/");
  await page.getByRole("navigation", { name: "Main menu" }).getByRole("link", { name: "Import" }).click();
  await expect(page.getByRole("heading", { name: "Import transactions", level: 1 })).toBeVisible();

  // Step 1: the drag text stays; "choose from your computer" is a full-width 44 px button.
  await expect(page.getByText("Drop the file here")).toBeVisible();
  const choose = page.getByRole("button", { name: "choose from your computer" });
  const zone = await page.locator(".drop-zone").boundingBox();
  const button = await choose.boundingBox();
  expect(button!.height).toBeGreaterThanOrEqual(44);
  expect(button!.width).toBeGreaterThan(zone!.width - 60);
  await expectAccessible(page, "import step 1 on a phone");
  await expectTouchTargets(page, ".import-file .form-actions");

  await page.getByLabel("File to import").setInputFiles("../tests/fixtures/directa/directa_synthetic_2024.xlsx");
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await page.getByLabel("Import into account").selectOption({ label: "Conto Directa" });
  await expectAccessible(page, "import account step on a phone");
  await expectTouchTargets(page, ".import-account-step .form-actions");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("tablist", { name: "Lines by category" })).toBeVisible();
  await expectAccessible(page, "import preview with its blocking card on a phone");
  const asset = (name: string) => page.locator(".import-asset", { hasText: name });
  await asset("TEST ETF WORLD UCITS").getByLabel("Type").selectOption({ label: "ETF" });
  await asset("TEST AZIONE SPA").getByLabel("Type").selectOption({ label: "Stock" });
  await expect(page.locator(".import-blocking")).toHaveCount(0);

  // The count cards two per row.
  const counts = await page.locator(".import-count").evaluateAll((list) =>
    list.map((el) => Math.round(el.getBoundingClientRect().top)),
  );
  expect(counts[0]).toBe(counts[1]);
  expect(counts[2]).toBe(counts[3]);
  expect(counts[2]).toBeGreaterThan(counts[0]);

  // Still a table for screen readers; each line a card: the tick, the date and the type; the
  // security; quantity | amount; cash | file rows; the notes.
  await expect(page.getByRole("table")).toHaveCount(1);
  await expect(page.getByRole("columnheader", { name: "Quantity" })).toHaveCount(1);
  const buy = page.locator(".import-table tbody tr", { hasText: "Jan 15, 2024" });
  const [tick, date, type, security, quantity, amount, cash, notes, rows] = await cellPlaces(buy);
  const card = await buy.boundingBox();
  expect(Math.abs(tick.middle - date.middle)).toBeLessThanOrEqual(2);
  expect(Math.abs(type.middle - date.middle)).toBeLessThanOrEqual(2);
  expect(security.top).toBeGreaterThan(date.bottom);
  expect(security.width).toBeGreaterThan(card!.width - 2);
  expect(quantity.top).toBe(amount.top);
  expect(quantity.top).toBeGreaterThan(security.bottom);
  expect(amount.left).toBe(type.left);
  expect(cash.top).toBe(rows.top);
  expect(cash.top).toBeGreaterThan(quantity.bottom);
  expect(rows.left).toBe(type.left);
  expect(notes.top).toBeGreaterThan(cash.bottom);
  expect(notes.width).toBeGreaterThan(card!.width - 2);
  await expect(buy.getByRole("cell", { name: "TEST ETF WORLD UCITS", exact: true })).toBeVisible();

  // Every tick in a 44 px area; the tab buttons and the tick-all buttons 44 px, side by side.
  const areas = await page.locator(".import-table label.tick").evaluateAll((list) =>
    list.map((el) => [el.getBoundingClientRect().width, el.getBoundingClientRect().height]),
  );
  expect(areas.length).toBe(11);
  for (const [width, height] of areas) {
    expect(width).toBeGreaterThanOrEqual(44);
    expect(height).toBeGreaterThanOrEqual(44);
  }
  await expectTouchTargets(page, ".import-preview .tabs");
  await expectTouchTargets(page, ".import-selection .actions");
  const tickAll = await page.locator(".import-selection .actions .button").evaluateAll((list) =>
    list.map((el) => Math.round(el.getBoundingClientRect().top)),
  );
  expect(tickAll[0]).toBe(tickAll[1]);
  await expectAccessible(page, "import preview on a phone");

  // Untick one line, confirm: the summary.
  await page.getByRole("checkbox", { name: "Import: Deposit, Jan 10, 2024" }).uncheck();
  await expect(page.locator(".import-count").first().locator("dd")).toHaveText("10");
  await expectTouchTargets(page, ".confirm-bar");
  await page.getByRole("button", { name: "Confirm the import of 10 transactions" }).click();
  await expect(
    page.getByRole("heading", { name: "Done: 10 transactions added to «Conto Directa»" }),
  ).toBeVisible();
  await expectAccessible(page, "import summary on a phone");
  await expectTouchTargets(page, ".import-done > .actions");
  await expectNoSidewaysScroll(page);
  await expectBarInView(page);
});

test("phone: the tabs wrap, every one in sight, none scrolled away", async ({ page }) => {
  await importInvestor(page, "phone-import-tabs");
  await previewDirecta(page);
  // "Do not move cash": a fourth tab, Skipped by choice.
  await page.getByLabel("Do not move cash").check();
  const tabs = page.getByRole("tab");
  await expect(tabs).toHaveText([
    "To import (7)",
    "To review (1)",
    "Skipped by choice (4)",
    "Not recognized (1)",
  ]);
  const seen = await page.getByRole("tablist").evaluate((list) => {
    const box = list.getBoundingClientRect();
    const tops = new Set<number>();
    const outside = [...list.querySelectorAll<HTMLElement>('[role="tab"]')].filter((tab) => {
      const t = tab.getBoundingClientRect();
      tops.add(Math.round(t.top));
      return t.left < box.left - 0.5 || t.right > box.right + 0.5;
    });
    return { lines: tops.size, outside: outside.length, scrolls: list.scrollWidth > list.clientWidth };
  });
  expect(seen.lines).toBeGreaterThanOrEqual(2);
  expect(seen).toEqual(expect.objectContaining({ outside: 0, scrolls: false }));
  // The active one, wherever it lands, keeps its look; the keys still move along the tabs.
  await page.getByRole("tab", { name: "Not recognized (1)" }).click();
  await expect(page.getByRole("tab", { name: "Not recognized (1)" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: "Skipped by choice (4)" })).toBeFocused();
  await expect(page.getByRole("tab", { name: "Skipped by choice (4)" })).toHaveAttribute("aria-selected", "true");
  await expectAccessible(page, "import preview, a second tab, on a phone");
});

test("phone: the confirm bar stays above the bottom bar, and Tab never hides under it", async ({ page }) => {
  await importInvestor(page, "phone-import-sticky");
  await previewDirecta(page);
  // While the preview scrolls the bar sticks 12 px above the bottom bar, both on top.
  const end = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
  for (const y of [400, 1200, 2000, Math.max(0, end - 900)]) {
    const seen = await confirmBarAt(page, y);
    expect(seen.position, `scrolled to ${y}`).toBe("sticky");
    expect(Math.abs(seen.navTop - 12 - seen.bottom), `scrolled to ${y}`).toBeLessThanOrEqual(1);
    expect(seen.onTop && seen.navOnTop, `scrolled to ${y}: both bars on top`).toBe(true);
  }
  // At the end of the page the last card is wholly above the bar, the bar above the bottom bar.
  const last = await confirmBarAt(page, end);
  expect(last.bottom).toBeLessThanOrEqual(last.navTop - 11);
  const lastCard = await page.locator(".import-table tbody tr").last().boundingBox();
  expect(lastCard!.y + lastCard!.height).toBeLessThanOrEqual(last.top);
  await expectBarInView(page);

  // Tab from the first tick to the last: each one is wholly in sight above the bar.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.locator(".import-table input").first().focus();
  for (let i = 0; i < 11; i++) {
    const place = await page.evaluate(() => {
      const focused = document.activeElement as HTMLElement;
      const box = focused.getBoundingClientRect();
      const bar = document.querySelector(".confirm-bar")!.getBoundingClientRect();
      return { inTable: !!focused.closest(".import-table"), top: box.top, bottom: box.bottom, bar: bar.top };
    });
    expect(place.inTable, `tab ${i}`).toBe(true);
    expect(place.top, `tab ${i}`).toBeGreaterThanOrEqual(0);
    expect(place.bottom, `tab ${i}: under the bar`).toBeLessThanOrEqual(place.bar);
    await page.keyboard.press("Tab");
  }
  // The next Tab reaches the bar itself.
  await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();
});

test.describe("the import at 320 px", () => {
  test.use({ viewport: { width: 320, height: 640 } });

  test("phone: long names stay inside on step 1, the preview and the summary", async ({ page }) => {
    await importInvestor(page, "phone-320-import");
    await page.goto("/import");
    await chooseMadeFile(page, LONG_FILE, LONG_CSV);
    await expect(page.getByText(/Chosen file: movements-of-an-example/)).toBeVisible();
    await expectNoSidewaysScroll(page);
    await expectBarInView(page);

    await previewCsv(page, LONG_FILE, LONG_CSV);
    await expect(page.locator(".import-blocking")).toBeVisible();
    await expect(page.locator(".import-table tbody tr").first()).toBeVisible();
    await expectNoSidewaysScroll(page);
    expect(await cellsOutsideTheirCards(page)).toEqual([]);
    await page.getByRole("tab", { name: "Not valid (1)" }).click();
    await expectNoSidewaysScroll(page);
    expect(await cellsOutsideTheirCards(page)).toEqual([]);
    await expectBarInView(page);
    await expectAccessible(page, "import preview with long names at 320 px");

    await previewCsv(page, "two-accounts.csv", TWO_ACCOUNTS_CSV);
    await page.getByRole("button", { name: "Confirm the import of 3 transactions" }).click();
    await expect(page.getByRole("heading", { name: "Done: 3 transactions added to 2 accounts" })).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
    await expectNoSidewaysScroll(page);
    await expectBarInView(page);
  });

  test("phone: the confirm bar stays above the bottom bar at 320 px", async ({ page }) => {
    await importInvestor(page, "phone-320-sticky");
    await previewDirecta(page);
    const seen = await confirmBarAt(page, 1600);
    expect(seen.position).toBe("sticky");
    expect(Math.abs(seen.navTop - 12 - seen.bottom)).toBeLessThanOrEqual(1);
    expect(seen.onTop && seen.navOnTop).toBe(true);
    await expectNoSidewaysScroll(page);
  });
});

// --- Add holdings, search and manual prices (D-194) ----------------------------------------

test("phone: add one holding I already own through a row card", async ({ page }) => {
  const { account, etf, bond } = await addInvestor(page, "phone-add");
  await page.goto(`/holdings/existing?account=${account.id}`);
  await expect(page.getByRole("heading", { name: "Add holdings I already own", level: 1 })).toBeVisible();
  // Still a table for screen readers: the row's header names it.
  const table = page.getByRole("table", { name: "Securities to add, one per row" });
  await expect(table.getByRole("rowheader")).toHaveCount(3);
  await expect(table.getByRole("columnheader", { name: "Average cost" })).toBeAttached();
  await page.getByLabel("Held on", { exact: true }).fill("31/12/2025");
  await page.getByLabel("Security, row 1").selectOption(etf.id);
  await page.getByLabel("Quantity, row 1").fill("10");
  await page.getByLabel("Average cost, row 1").fill("100");
  await page.getByLabel("Security, row 2").selectOption(bond.id);
  await expect(page.getByLabel("Nominal, row 2")).toBeVisible();
  await expect(table.locator("tbody tr").nth(1).locator('td[data-label="Nominal"]')).toBeVisible();

  // The card: "Row 1" and its × on the first line, the security wide, then quantity | average
  // cost side by side, the total wide; framed, never wider than the window.
  const card = table.locator("tbody tr").first();
  await expect(card).toContainText("€1,000.00");
  const { card: frame, cells } = await cardBoxes(card);
  const [number, security, quantity, price, total, remove] = cells;
  expect(Math.abs(number.top + number.bottom - remove.top - remove.bottom)).toBeLessThanOrEqual(2);
  expect(remove.right).toBeGreaterThan(frame.right - 20);
  expect(remove.width).toBeGreaterThanOrEqual(44);
  expect(security.top).toBeGreaterThan(number.bottom - 1);
  expect(security.width).toBeGreaterThan(frame.width - 40);
  expect(quantity.top).toBe(price.top);
  expect(price.left).toBeGreaterThan(quantity.right);
  expect(total.top).toBeGreaterThan(quantity.top);
  expect(total.width).toBeGreaterThan(frame.width - 40);
  expect(await card.evaluate((tr) => getComputedStyle(tr).borderTopStyle)).toBe("solid");
  await expect(card.getByRole("rowheader")).toHaveText("1"); // "Row" is drawn, not read
  await expect(card.getByRole("rowheader")).toHaveAttribute("data-label", "Row");
  expect(await fullWidthActions(page, ".add-actions")).toEqual([true, true]);
  await expectNoSidewaysScroll(page);
  await expectTouchTargets(page, "table.add-rows");
  await expectTouchTargets(page, ".add-rows-foot");
  await expectTouchTargets(page, ".add-actions");
  await expectAccessible(page, "add holdings on a phone");
  await page.emulateMedia({ colorScheme: "dark" });
  await expectAccessible(page, "add holdings on a phone, dark");
  await page.emulateMedia({ colorScheme: "light" });

  // "New security…" opens the full-screen dialog; a security made there fills the row.
  await page.getByLabel("Security, row 2").selectOption("");
  await page.getByRole("button", { name: "New security for row 3" }).click();
  const dialog = await expectFullScreen(page, "Add a security");
  await dialog.getByLabel("Name", { exact: true }).fill("Phone Example Made Here");
  await dialog.getByLabel("Type").selectOption("ETF");
  await dialog.getByRole("button", { name: "Create security" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByLabel("Security, row 3")).toHaveValue(/.+/);
  await page.getByRole("button", { name: "Remove row 3" }).click();

  await page.getByRole("button", { name: "Save 1 security" }).click();
  await expect(page).toHaveURL(`/holdings?account=${account.id}`);
  await expect(page.getByText("1 security added to “Phone account”.")).toBeVisible();
  await expect(page.getByRole("row", { name: /Phone Example World ETF/ })).toBeVisible();
});

test("phone: a row the server refuses is marked on its card", async ({ page }) => {
  const { account, etf, bond } = await addInvestor(page, "phone-add-refused");
  await page.goto(`/holdings/existing?account=${account.id}`);
  await page.getByLabel("Security, row 1").selectOption(etf.id);
  await page.getByLabel("Quantity, row 1").fill("10");
  await page.getByLabel("Average cost, row 1").fill("100");
  // Too large a cost to store: the server's dry run refuses the row (D-98).
  await page.getByLabel("Security, row 2").selectOption(bond.id);
  await page.getByLabel("Nominal, row 2").fill("999999999999999");
  await page.getByLabel("Average cost, row 2").fill("999999999999999");
  await page.getByRole("button", { name: "Save 2 securities" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Check row 2." })).toBeVisible();
  await expect(page.getByLabel("Average cost, row 2")).toBeFocused();
  await expect(page.getByLabel("Average cost, row 2")).toHaveAccessibleDescription(/too many digits/i);

  // The error under its field, inside the card; a red line on the card's left, not the other's.
  const cards = page.locator("table.add-rows tbody tr");
  const shadow = (n: number) => cards.nth(n).evaluate((tr) => getComputedStyle(tr).boxShadow);
  expect(await shadow(1)).toMatch(/inset/);
  expect(await shadow(0)).toBe("none");
  const error = cards.nth(1).locator(".field-error").first();
  await expect(error).toBeVisible();
  const { card } = await cardBoxes(cards.nth(1));
  const box = (await error.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(card.left);
  expect(box.x + box.width).toBeLessThanOrEqual(card.right);
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "add holdings with a refused row on a phone");
  await page.emulateMedia({ colorScheme: "dark" });
  await expectAccessible(page, "add holdings with a refused row on a phone, dark");
});

test("phone: a manual price on the holding's page; the list stays a table", async ({ page }) => {
  const { account, etf } = await addInvestor(page, "phone-price", { held: true });
  await page.goto(`/holdings/${etf.id}/prices?account=${account.id}`);
  await expect(page.getByRole("heading", { name: "Manual prices", level: 1 })).toBeVisible();
  await page.getByLabel("Price (EUR)").fill("104.20");
  await page.getByRole("button", { name: "Save price" }).click();
  await expect(page.getByRole("status").filter({ hasText: /^Price of .+ saved\.$/ })).toBeVisible();
  const table = page.getByRole("table", { name: "Your manual prices" });
  await expect(table.getByRole("columnheader", { name: "Price" })).toBeVisible(); // a table, not cards
  await expect(table.getByRole("cell", { name: "€104.20" })).toBeVisible();
  expect(await fullWidthActions(page, ".manual-price-form")).toEqual([true]);
  await expectTouchTargets(page, ".manual-prices-table");
  await expectTouchTargets(page, ".manual-price-form .actions");
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "a holding's manual prices on a phone");
  await page.emulateMedia({ colorScheme: "dark" });
  await expectAccessible(page, "a holding's manual prices on a phone, dark");
});

test("phone: every manual price at once, a row refused by the server and one saved", async ({ page }) => {
  const { stock, bought } = await addInvestor(page, "phone-all-prices", { held: true });
  await page.goto("/holdings/prices");
  await expect(page.getByRole("heading", { name: "Update prices by hand", level: 1 })).toBeVisible();
  const table = page.getByRole("table", { name: "Open holdings whose prices you enter by hand" });
  await expect(table.getByRole("row")).toHaveCount(4); // the header row and three cards
  // Each holding a card: the name, then the last price, then the field, each on its own line.
  const card = table.getByRole("row", { name: /Phone Example BTP 2031/ });
  const { cells } = await cardBoxes(card);
  expect(cells[1].top).toBeGreaterThan(cells[0].top);
  expect(cells[2].top).toBeGreaterThan(cells[1].top);
  await expect(card.getByLabel("Phone Example BTP 2031: % of face value, clean price")).toBeVisible();
  const field = (await card.getByRole("textbox").boundingBox())!;
  expect(field.width).toBeGreaterThan(cells[2].width - 2);
  await expect(card.getByRole("status")).toBeAttached(); // always in the page, empty

  // Deleted in another tab while the page is open: the server refuses that row (404).
  await deleteElsewhere(page, bought[stock.id], stock.id);
  await page.getByLabel("Phone Example Industries: Price (EUR)").fill("13");
  await page.getByRole("button", { name: "Save prices" }).click();
  const refused = table.getByRole("row", { name: /Phone Example Industries/ });
  await expect(refused.getByText("Not found: it may have been deleted meanwhile.")).toBeVisible();
  await expect(page.getByLabel("Phone Example Industries: Price (EUR)")).toBeFocused();
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "manual prices of all with a refused row on a phone");
  expect(await fullWidthActions(page, ".manual-prices-all")).toEqual([true]);
  await expectTouchTargets(page, ".manual-prices-all-table");
  await expectTouchTargets(page, ".manual-prices-all > .actions");

  // Then a price saved: its own status under its field. A save refreshes the holdings, so the
  // deleted security leaves the list.
  await page.getByLabel("Phone Example BTP 2031: % of face value, clean price").fill("99.40");
  await page.getByLabel("Phone Example Industries: Price (EUR)").fill("");
  await page.getByRole("button", { name: "Save prices" }).click();
  await expect(card.getByRole("status")).toHaveText("Saved");
  await expect(table.getByRole("row", { name: /Phone Example Industries/ })).toHaveCount(0);
  await expect(table.getByRole("row", { name: /Phone Example World ETF/ })).toBeVisible();
  await page.emulateMedia({ colorScheme: "dark" });
  await expectAccessible(page, "manual prices of all with a saved row on a phone, dark");
});

test.describe("search and create on the market server", () => {
  test.use({ baseURL: MARKET });

  test("phone: search, add a result, check its price source, then a bond with none", async ({ page }) => {
    await logIn(page, await marketInvestor("phone-search"));
    const account = await apiPost<{ id: string }>(page, "/accounts", { name: "Phone market", currency: "EUR" });
    await page.goto(`/holdings/add?account=${account.id}`);
    await page.getByLabel("Search by ISIN, name or symbol").fill(MARKET_STOCK_ISIN);
    const search = page.getByRole("button", { name: "Search", exact: true });
    await search.click();
    await expect(page.getByText("1 result")).toBeVisible();
    // "Search" stays beside the box.
    const box = (await page.getByLabel("Search by ISIN, name or symbol").boundingBox())!;
    const button = (await search.boundingBox())!;
    expect(button.x).toBeGreaterThan(box.x + box.width);
    // A result: the security, then its button under it.
    const add = page.getByRole("button", { name: "Add “Example Market Industrial SpA” (ZSTK.MI, EUR)" });
    const result = page.locator(".search-result", { has: add });
    const text = (await result.locator("strong").boundingBox())!;
    expect((await add.boundingBox())!.y).toBeGreaterThan(text.y + text.height);
    await expectTouchTargets(page, ".search-form");
    await expectTouchTargets(page, ".search-results");
    await expectNoSidewaysScroll(page);
    await expectAccessible(page, "search results on a phone");

    await add.click();
    await expect(page.getByLabel("ISIN (optional)")).toHaveValue(MARKET_STOCK_ISIN);
    const check = page.getByRole("button", { name: "Check the price source" });
    const answer = page.getByRole("status").and(page.locator(".price-check-result"));
    await check.click();
    await expect(answer).toContainText(/^Expected source: Yahoo Finance · Milan · latest price €24\.75 on /);
    const form = (await page.locator(".asset-create-form").boundingBox())!;
    expect(Math.round((await check.boundingBox())!.width)).toBe(Math.round(form.width));
    expect(await check.evaluate((el) => getComputedStyle(el.parentElement!).position)).toBe("static");
    expect(await fullWidthActions(page, ".asset-create-form")).toEqual([true, true]);
    await expectTouchTargets(page, ".asset-create-form");
    await expectNoSidewaysScroll(page);
    await expectAccessible(page, "the price check found on a phone");
    await page.emulateMedia({ colorScheme: "dark" });
    await expectAccessible(page, "the price check found on a phone, dark");
    await page.emulateMedia({ colorScheme: "light" });

    // A bond no provider knows: no source; the security is created all the same.
    await page.getByLabel("Type").selectOption("BOND");
    await page.getByLabel("ISIN (optional)").fill(MARKET_BOND_ISIN);
    await check.click();
    await expect(answer).toHaveText("No source: prices are entered by hand. No provider knows this code.");
    await expectNoSidewaysScroll(page);
    await page.getByLabel("Name", { exact: true }).fill("Phone Example Check Bond");
    await page.getByRole("button", { name: "Create security" }).click();
    await expect(page.getByText(/^“Phone Example Check Bond” added\./)).toBeVisible();
    await expect(page.getByRole("link", { name: "Add holdings I already own" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Add a transaction" })).toBeVisible();
    await expectNoSidewaysScroll(page);
  });

  test.describe("at 320 px", () => {
    test.use({ viewport: { width: 320, height: 640 } });

    test("phone: the search results and the create form stay inside the window", async ({ page }) => {
      await logIn(page, await marketInvestor("phone-search-320"));
      await page.goto("/holdings/add");
      await page.getByLabel("Search by ISIN, name or symbol").fill("example text");
      const search = page.getByRole("button", { name: "Search", exact: true });
      await search.click();
      await expect(page.getByText("1 result")).toBeVisible();
      const box = (await page.getByLabel("Search by ISIN, name or symbol").boundingBox())!;
      expect((await search.boundingBox())!.x).toBeGreaterThan(box.x + box.width); // still beside
      await expectNoSidewaysScroll(page);
      expect(await outsideTheWindow(page)).toEqual([]);
      await page.getByRole("button", { name: /^Add “Example Market Text Holding”/ }).click();
      await page.getByLabel("Name", { exact: true }).fill(ONE_WORD);
      await page.getByLabel("ISIN (optional)").fill(MARKET_STOCK_ISIN);
      await page.getByLabel("Type").selectOption("STOCK");
      await page.getByRole("button", { name: "Check the price source" }).click();
      await expect(page.locator(".price-check-result")).toContainText("Expected source");
      await expectNoSidewaysScroll(page);
      expect(await outsideTheWindow(page)).toEqual([]);
      await page.getByRole("button", { name: "Create security" }).click();
      await expect(page.getByText(new RegExp(`^“${ONE_WORD}” added\\.`))).toBeVisible();
      await expectNoSidewaysScroll(page);
      expect(await outsideTheWindow(page)).toEqual([]);
    });
  });
});

test.describe("add holdings and manual prices at 320 px", () => {
  test.use({ viewport: { width: 320, height: 640 } });

  test("phone: long names stay inside the add-holdings cards, the dialog and the manual prices", async ({
    page,
  }) => {
    const { account, etf, bond } = await addInvestor(page, "phone-320-add", {
      held: true,
      names: [ONE_WORD, PHRASE, "Phone Example Industries"],
    });
    await page.goto(`/holdings/existing?account=${account.id}`);
    await page.getByLabel("Security, row 1").selectOption(etf.id);
    await page.getByLabel("Quantity, row 1").fill("10");
    await page.getByLabel("Average cost, row 1").fill("100");
    await page.getByLabel("Security, row 2").selectOption(bond.id);
    await page.getByLabel("Nominal, row 2").fill("1000");
    await page.getByLabel("Average cost, row 2").fill("98");
    await page.getByLabel("Security, row 3").selectOption(etf.id); // "also in row 1"
    await expect(page.locator("table.add-rows tbody tr").nth(1)).toContainText("€980.00");
    await expectNoSidewaysScroll(page);
    expect(await outsideTheWindow(page)).toEqual([]);
    await expectBarInView(page);

    await page.getByRole("button", { name: "New security for row 1" }).click();
    await expectFullScreen(page, "Add a security");
    await expectNoSidewaysScroll(page);
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();

    await page.goto(`/holdings/${etf.id}/prices`);
    await page.getByLabel("Price (EUR)").fill("104.20");
    await page.getByRole("button", { name: "Save price" }).click();
    await expect(page.getByRole("table", { name: "Your manual prices" }).getByRole("cell", { name: "€104.20" })).toBeVisible();
    await expectNoSidewaysScroll(page);
    expect(await outsideTheWindow(page)).toEqual([]);

    await page.goto("/holdings/prices");
    await expect(page.getByRole("link", { name: ONE_WORD })).toBeVisible();
    await page.getByLabel(`${PHRASE}: % of face value, clean price`).fill("abc");
    await expectNoSidewaysScroll(page);
    expect(await outsideTheWindow(page)).toEqual([]);
    await expectBarInView(page);
  });
});
