// The flow both compact specs follow (D-189): phone.spec.ts at 390x844 with touch,
// tablet.spec.ts at 820x1180. Each test makes its own users, so the seeded data stays as the
// other specs expect it.
import type { Page } from "@playwright/test";

import { expect } from "./fixtures";
import {
  apiPost,
  dashboardHeading,
  ADMIN,
  DEMO,
  expectAccessible,
  expectBarInView,
  expectNoSidewaysScroll,
  expectTouchTargets,
  logIn,
  newUser,
} from "./helpers";

const SHELL = "header, nav[aria-label='Main menu']";

function bar(page: Page) {
  return page.getByRole("navigation", { name: "Main menu" });
}

async function openMore(page: Page) {
  await bar(page).getByRole("button", { name: "More" }).click();
  const menu = page.getByRole("dialog", { name: "More" });
  await expect(menu).toBeVisible();
  return menu;
}

/** Logs in, moves through the bar and the menu, changes the account in view, logs out. */
export async function walkTheShell(page: Page, prefix: string): Promise<void> {
  const user = await newUser(prefix);
  await logIn(page, user);
  const first = await apiPost<{ id: string }>(page, "/accounts", { name: "Conto uno", currency: "EUR" });
  await apiPost(page, "/accounts", { name: "Conto due", currency: "EUR" });
  await page.reload();
  await expect(page.getByRole("heading", { name: dashboardHeading(user), level: 1 })).toBeVisible();

  // No sidebar: a top strip and the bar of five entries.
  await expect(page.getByRole("complementary")).toHaveCount(0);
  await expect(page.getByRole("banner")).toBeVisible();
  await expect(bar(page).getByRole("listitem")).toHaveText([
    "Dashboard",
    "Holdings",
    "Transactions",
    "Import",
    "More",
  ]);
  await expectNoSidewaysScroll(page);
  await expectTouchTargets(page, SHELL);
  await expectAccessible(page, `the compact shell (${prefix})`);

  // The bar's pages.
  for (const [entry, heading] of [
    ["Holdings", "Holdings"],
    ["Transactions", "Transactions"],
    ["Import", "Import transactions"],
  ]) {
    await bar(page).getByRole("link", { name: entry, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
    await expect(bar(page).getByRole("link", { name: entry, exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expectNoSidewaysScroll(page);
  }

  // The menu's pages; Escape closes it and gives the focus back to "More".
  let menu = await openMore(page);
  await expectTouchTargets(page, "dialog");
  await expectAccessible(page, `the open menu (${prefix})`);
  await expect(menu.getByRole("link", { name: "Administration" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(bar(page).getByRole("button", { name: "More" })).toBeFocused();
  for (const [entry, heading] of [
    ["Accounts", "Accounts"],
    ["Settings", "Settings"],
    [/^About/, "About"],
  ] as const) {
    menu = await openMore(page);
    await menu.getByRole("link", { name: entry }).click();
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
    await expect(menu).toHaveCount(0);
  }

  // The account in view, from the strip; kept by the bar's links.
  await page.getByRole("combobox", { name: "Account in view" }).selectOption({ label: "Conto uno" });
  await expect(page).toHaveURL(`/about?account=${first.id}`);
  await bar(page).getByRole("link", { name: "Dashboard" }).click();
  await expect(page).toHaveURL(`/?account=${first.id}`);

  // Log out from the menu.
  menu = await openMore(page);
  await menu.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByLabel("Username")).toBeVisible();
}

/** An administrator finds Administration in the menu. */
export async function administrationInTheMenu(page: Page, prefix: string): Promise<void> {
  const admin = await newUser(prefix, { role: "ADMINISTRATOR" });
  await logIn(page, admin);
  const menu = await openMore(page);
  await menu.getByRole("link", { name: "Administration" }).click();
  await expect(page.getByRole("heading", { name: "Administration", level: 1 })).toBeVisible();
  await expectNoSidewaysScroll(page);
  await expectBarInView(page);
  await expectAccessible(page, `Administration in the compact shell (${prefix})`);
}

/** Tab reaches the bar's entries, each with a visible ring in the bar's text colour. */
export async function focusRingOnTheBar(page: Page, prefix: string): Promise<void> {
  const user = await newUser(prefix);
  await logIn(page, user);
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(() => document.activeElement?.classList.contains("bottom-nav-item"))) break;
  }
  const seen: string[] = [];
  for (let i = 0; i < 5; i++) {
    const ring = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement;
      const style = getComputedStyle(el);
      return {
        entry: el.textContent ?? "",
        inBar: el.classList.contains("bottom-nav-item"),
        visible: el.matches(":focus-visible"),
        outline: `${style.outlineStyle} ${style.outlineWidth}`,
        same: style.outlineColor === style.color,
      };
    });
    expect(ring, `the focus ring on ${ring.entry}`).toEqual({
      entry: ring.entry,
      inBar: true,
      visible: true,
      outline: "solid 3px",
      same: true,
    });
    seen.push(ring.entry);
    await page.keyboard.press("Tab");
  }
  expect(seen).toEqual(["Dashboard", "Holdings", "Transactions", "Import", "More"]);
}

/**
 * The pages that hold the widest content (cards, tables, the colour swatches), with the
 * invented development data: the window never scrolls sideways and the bar stays on screen at
 * the bottom of the page, so navigation is always possible.
 */
export async function wideContentStaysInside(page: Page, pages: readonly string[]): Promise<void> {
  // The development data's users work in Italian.
  const headings: Record<string, string> = {
    "/transactions": "Transazioni",
    "/settings": "Impostazioni",
    "/admin": "Amministrazione",
  };
  for (const [user, paths] of [
    [DEMO, pages.filter((p) => p !== "/admin")],
    [ADMIN, pages.filter((p) => p === "/admin")],
  ] as const) {
    if (paths.length === 0) continue;
    await logIn(page, user);
    for (const path of paths) {
      await page.goto(path);
      const heading = path === "/" ? dashboardHeading(user) : headings[path];
      await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expectNoSidewaysScroll(page);
      await expectBarInView(page);
    }
    await page.goto("/");
    await page
      .getByRole("navigation", { name: "Menu principale" })
      .getByRole("button", { name: "Altro" })
      .click();
    const menu = page.getByRole("dialog", { name: "Altro" });
    await menu.getByRole("button", { name: "Esci" }).click();
    await expect(page.getByLabel("Nome utente")).toBeVisible();
    // The next login starts from the browser's language, as logIn expects.
    await page.evaluate(() => localStorage.removeItem("pt.language"));
  }
}
