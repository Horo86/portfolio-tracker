import { administrationInTheMenu, walkTheShell } from "./compactShell";
import { expect, test } from "./fixtures";
import {
  dashboardHeading,
  DEMO,
  expectBarInView,
  expectNoSidewaysScroll,
  logIn,
  newUser,
} from "./helpers";

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
