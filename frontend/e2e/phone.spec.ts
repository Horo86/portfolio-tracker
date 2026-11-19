import type { Page } from "@playwright/test";

import {
  administrationInTheMenu,
  focusRingOnTheBar,
  walkTheShell,
  wideContentStaysInside,
} from "./compactShell";
import { expect, test } from "./fixtures";
import {
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
