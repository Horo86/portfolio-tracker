import type { Page } from "@playwright/test";

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
