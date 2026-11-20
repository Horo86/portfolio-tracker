import { administrationInTheMenu, walkTheShell } from "./compactShell";
import { expect, test } from "./fixtures";
import {
  dashboardHeading,
  DEMO,
  expectAccessible,
  expectBarInView,
  expectNoSidewaysScroll,
  logIn,
  newUser,
} from "./helpers";
import { confirmBarAt, importInvestor, previewDirecta } from "./importPhone";
import { accountsInvestor, boxes, directions } from "./remainingPhone";

// A tablet in portrait (D-189): 820x1180, the desktop page with the compact shell.
test.use({ viewport: { width: 820, height: 1180 } });

test("tablet: the bar, the menu, the account in view and log out", async ({ page }) => {
  await walkTheShell(page, "tablet");
});

test("tablet: an administrator reaches Administration from the menu", async ({ page }) => {
  await administrationInTheMenu(page, "tablet-admin");
});

test("tablet: a dialog keeps its desktop size (D-190)", async ({ page }) => {
  const admin = await newUser("tablet-dialog", { role: "ADMINISTRATOR" });
  await logIn(page, admin);
  await page.goto("/admin");
  await page.getByRole("button", { name: "New user" }).click();
  const box = (await page.getByRole("dialog", { name: "New user" }).boundingBox())!;
  expect(box.width).toBe(440);
  expect(box.height).toBeLessThan(1180);
  expect(box.x).toBeGreaterThan(0);
});

// Dashboard and holdings on a tablet (D-191): the desktop pages, nothing sideways.

test("tablet: the dashboard keeps the desktop page, its charts drawn", async ({ page }) => {
  await logIn(page, DEMO);
  await expect(page.getByRole("heading", { level: 1, name: dashboardHeading(DEMO) })).toBeVisible();
  await expect(page.getByRole("table", { name: "Per tipo" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  for (const chart of [".chart-donut", ".chart-bar"]) {
    await expect(page.locator(`${chart} svg`).first(), chart).toBeVisible();
  }
  await expectNoSidewaysScroll(page);
  await expectBarInView(page);
});

test("tablet: the holdings stay a table that scrolls inside its card", async ({ page }) => {
  await logIn(page, DEMO);
  await page.goto("/holdings");
  const table = page.getByRole("table", { name: /titoli/ });
  await expect(table).toBeVisible();
  const seen = await table.evaluate((element) => ({
    row: getComputedStyle(element.querySelector("tbody tr")!).display,
    inside:
      element.closest(".table-scroll")!.getBoundingClientRect().right <=
      element.closest(".card")!.getBoundingClientRect().right,
  }));
  expect(seen).toEqual({ row: "table-row", inside: true });
  await expectNoSidewaysScroll(page);
  await expectBarInView(page);
});

// Transactions on a tablet (D-192): the desktop table; its selection bar never sticks.

test("tablet: the transactions keep the desktop table", async ({ page }) => {
  await logIn(page, DEMO);
  await page.goto("/transactions");
  const table = page.getByRole("table");
  await expect(table).toBeVisible();
  await page.getByRole("checkbox", { name: /^Seleziona: / }).first().check();
  const seen = await table.evaluate((element) => ({
    row: getComputedStyle(element.querySelector("tbody tr")!).display,
    header: getComputedStyle(element.querySelector("thead tr")!).display,
    bar: getComputedStyle(document.querySelector(".tx-selection-bar")!).position,
    selectText: element.querySelector(".tick-text")!.getBoundingClientRect().width <= 1,
  }));
  expect(seen).toEqual({ row: "table-row", header: "table-row", bar: "static", selectText: true });
  await expectNoSidewaysScroll(page);
  await expectBarInView(page);
});

test("tablet: the import preview keeps the desktop table, its confirm bar above the bottom bar", async ({
  page,
}) => {
  await importInvestor(page, "tablet-import");
  await previewDirecta(page);
  // The desktop table: its header in sight, a row a table row (D-193 changes phones only).
  await expect(page.getByRole("columnheader", { name: "Quantity" })).toBeVisible();
  const display = await page
    .locator(".import-table tbody tr")
    .first()
    .evaluate((row) => getComputedStyle(row).display);
  expect(display).toBe("table-row");
  // The bar sticks as on desktop, 12 px above the bottom bar, never over it.
  for (const y of [300, 900]) {
    const seen = await confirmBarAt(page, y);
    expect(seen.position, `scrolled to ${y}`).toBe("sticky");
    expect(seen.bottom, `scrolled to ${y}`).toBeLessThanOrEqual(seen.navTop - 11);
    expect(seen.onTop && seen.navOnTop, `scrolled to ${y}: both bars on top`).toBe(true);
  }
  const buttons = await page.locator(".confirm-bar .actions .button").evaluateAll((list) =>
    list.map((el) => Math.round(el.getBoundingClientRect().top)),
  );
  expect(buttons[0]).toBe(buttons[1]);
  await expectNoSidewaysScroll(page);
  await expectBarInView(page);
});

test("tablet: add holdings and the manual prices keep the desktop tables (D-194)", async ({ page }) => {
  await logIn(page, DEMO);
  const display = (selector: string) =>
    page.locator(selector).evaluate((table) => ({
      row: getComputedStyle(table.querySelector("tbody tr")!).display,
      header: getComputedStyle(table.querySelector("thead tr")!).display,
      headerWidth: Math.round(table.querySelector("thead th:nth-child(2)")!.getBoundingClientRect().width) > 1,
    }));
  const desktop = { row: "table-row", header: "table-row", headerWidth: true };

  await page.goto("/holdings/existing");
  await expect(page.getByLabel("Titolo, riga 1")).toBeVisible();
  expect(await display("table.add-rows")).toEqual(desktop);
  expect(await page.locator("table.add-rows tbody tr").first().evaluate((tr) => getComputedStyle(tr).borderTopStyle)).toBe("none");
  await expectNoSidewaysScroll(page);

  await page.goto("/holdings/prices");
  await expect(page.getByRole("heading", { name: "Aggiorna i prezzi a mano", level: 1 })).toBeVisible();
  expect(await display("table.manual-prices-all-table")).toEqual(desktop);
  await expectNoSidewaysScroll(page);

  await page.getByRole("table").getByRole("link").first().click();
  await page.getByRole("link", { name: "Prezzi a mano", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Prezzi a mano", level: 1 })).toBeVisible();
  expect(await display("table.manual-prices-table")).toEqual(desktop);
  // "Modifica" and "Elimina" are 44 px touch targets below 1024 px.
  const actions = await page.locator(".manual-prices-table .row-actions .link-button").evaluateAll((list) =>
    list.map((el) => Math.round(Math.min(el.getBoundingClientRect().width, el.getBoundingClientRect().height))),
  );
  expect(actions.length).toBeGreaterThan(0);
  for (const size of actions) expect(size).toBeGreaterThanOrEqual(44);
  await expectNoSidewaysScroll(page);
  await expectBarInView(page);
});

test("tablet: accounts, settings and About keep the desktop layout (D-195)", async ({ page }) => {
  await accountsInvestor(page, "tablet-remaining");
  await page.goto("/accounts");
  const card = page.getByRole("region", { name: "Conto uno" });
  await expect(card.locator(".figures dd").first()).toContainText("€");
  expect(await directions(page, ".account-head")).toEqual(["row", "row"]);
  const figures = await boxes(card, ".figures > div");
  expect(new Set(figures.map((f) => f.top)).size, "four figures on one line").toBe(1);
  await expectNoSidewaysScroll(page);

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  expect(new Set(await directions(page, ".settings-section .row"))).toEqual(new Set(["row"]));
  await expectNoSidewaysScroll(page);

  await page.goto("/about");
  await expect(page.getByRole("button", { name: /Release notes$/ })).toBeVisible();
  expect(new Set(await directions(page, ".about-rows .row"))).toEqual(new Set(["row"]));
  await expectNoSidewaysScroll(page);
  await expectAccessible(page, "About on a tablet");
});
