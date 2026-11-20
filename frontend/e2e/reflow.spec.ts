import type { Page } from "@playwright/test";

import { MARKET } from "../playwright.config";
import { expect, test } from "./fixtures";
import {
  apiPost,
  dashboardHeading,
  expectBarInView,
  expectNoSidewaysScroll,
  logIn,
  newUser,
  nextCode,
} from "./helpers";
import { DIRECTA_FIXTURE, importInvestor } from "./importPhone";
import { longNamesUser, ONE_WORD, PHRASE } from "./longNames";
import { marketInvestor } from "./marketUsers";
import { longNames, openPriceSources } from "./priceSourcesPage";
import {
  accountsInvestor,
  confirmEnrolment,
  enrolToQr,
  fakeFirstRun,
  fakeTwoFactorObligation,
  firstRunToLanguage,
} from "./remainingPhone";

// Reflow (WCAG 1.4.10, D-189, D-195): at 320 px every page fits the window. Nothing scrolls
// sideways, no element is wider than the window (but inside a box that scrolls on its own, a
// wide table's `.table-scroll`), and on the pages with a session the bottom bar is in view at
// exactly its 64 px. The long-named securities (longNames.ts) are on the pages that show them.
// Chromium only, in the default project (no Playwright project of its own, B12).
test.use({ viewport: { width: 320, height: 640 }, hasTouch: true, isMobile: true });

/** The elements wider than the window or out of it, those inside a box of their own that
 * scrolls or clips left out (as the screen-reader-only texts). */
async function widerThanTheWindow(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const clipped = (el: Element) => {
      for (let up = el.parentElement; up && up !== document.body; up = up.parentElement) {
        if (getComputedStyle(up).overflowX !== "visible") return true;
      }
      return false;
    };
    return [...document.querySelectorAll<HTMLElement>("body *")]
      .filter((el) => el.getClientRects().length > 0 && !el.closest(".visually-hidden"))
      .filter((el) => {
        const box = el.getBoundingClientRect();
        return box.width > 0 && (box.left < -0.5 || box.right > width + 0.5 || box.width > width + 0.5);
      })
      .filter((el) => !clipped(el))
      .map((el) => `${el.tagName}.${el.className}`);
  });
}

/** The page as it is now fits 320 px; with a session, the bottom bar is there at 64 px, in
 * view unless a dialog (full screen on a phone) covers it. */
async function fits(page: Page, what: string, { bar = true, dialog = false } = {}): Promise<void> {
  await page.waitForLoadState("networkidle");
  await expectNoSidewaysScroll(page);
  expect(await widerThanTheWindow(page), `wider than the window: ${what}`).toEqual([]);
  const nav = page.locator(".bottom-nav");
  if (!bar) {
    await expect(nav, `no bottom bar on ${what}`).toHaveCount(0);
    return;
  }
  await expect(nav).toBeVisible();
  expect((await nav.boundingBox())!.height, `the bar's height on ${what}`).toBe(64);
  if (!dialog) await expectBarInView(page);
}

async function heading(page: Page, name: string | RegExp) {
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
}

test("reflow: the investor's pages at 320 px, with long names", async ({ page }) => {
  test.setTimeout(90_000);
  const assets = await longNamesUser(page);
  const one = assets[ONE_WORD];
  await page.goto("/");
  await expect(page.getByRole("link", { name: ONE_WORD }).first()).toBeVisible();
  await fits(page, "dashboard");

  await page.goto("/holdings");
  await expect(page.getByRole("link", { name: PHRASE })).toBeVisible();
  await fits(page, "holdings");
  await page.goto(`/holdings/${one}`);
  await heading(page, ONE_WORD);
  await fits(page, "a holding");

  await page.goto("/transactions");
  await heading(page, "Transactions");
  await expect(page.getByRole("cell", { name: ONE_WORD }).first()).toBeVisible();
  await fits(page, "transactions");
  await page.goto("/transactions/new");
  await heading(page, "New transaction");
  await fits(page, "the transaction form");
  await page.goto("/transactions");
  await page.getByRole("row", { name: new RegExp(PHRASE) }).getByRole("link", { name: "Edit" }).click();
  await heading(page, "Edit transaction");
  await fits(page, "the edit page");

  await page.goto("/holdings/existing");
  await heading(page, "Add holdings I already own");
  await page.getByLabel("Security, row 1").selectOption(one);
  await fits(page, "add holdings");
  await page.goto("/holdings/add");
  await heading(page, "Add a security");
  await fits(page, "search and create");
  await page.goto(`/holdings/${one}/prices`);
  await expect(page.getByRole("table", { name: "Your manual prices" })).toBeVisible();
  await fits(page, "one holding's manual prices");
  await page.goto("/holdings/prices");
  await expect(page.getByRole("link", { name: ONE_WORD })).toBeVisible();
  await fits(page, "every manual price");

  await page.goto("/about");
  await expect(page.getByRole("button", { name: /Release notes$/ })).toBeVisible();
  await page.getByText(/^Show the list/).click();
  await fits(page, "about");
  await page.goto("/no-such-page");
  await heading(page, "Page not found");
  await fits(page, "not found");
});

test("reflow: accounts and settings at 320 px, with a long account name", async ({ page }) => {
  await accountsInvestor(page, "reflow-accounts", { name: ONE_WORD, provider: PHRASE.slice(0, 100) });
  await page.goto("/accounts");
  await expect(page.getByRole("region", { name: ONE_WORD }).locator(".figures dd").first()).toContainText("€");
  await fits(page, "accounts");
  await page.goto("/settings");
  await heading(page, "Settings");
  await expect(page.locator(".sessions li")).toHaveCount(1);
  await fits(page, "settings");
});

test("reflow: the import at 320 px, every step up to the summary", async ({ page }) => {
  await importInvestor(page, "reflow-import");
  await page.goto("/import");
  await heading(page, "Import transactions");
  await fits(page, "import step 1");
  await page.getByLabel("File to import").setInputFiles(DIRECTA_FIXTURE);
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await expect(page.getByLabel("Import into account")).toBeVisible();
  await fits(page, "import, the account step");
  await page.getByLabel("Import into account").selectOption({ label: "Conto Directa" });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("tablist", { name: "Lines by category" })).toBeVisible();
  await fits(page, "the preview with its blocking card");
  const asset = (name: string) => page.locator(".import-asset", { hasText: name });
  await asset("TEST ETF WORLD UCITS").getByLabel("Type").selectOption({ label: "ETF" });
  await asset("TEST AZIONE SPA").getByLabel("Type").selectOption({ label: "Stock" });
  await expect(page.locator(".import-blocking")).toHaveCount(0);
  await fits(page, "the preview, ready");
  await page.getByRole("button", { name: /^Confirm the import of \d+ transactions$/ }).click();
  await expect(page.getByRole("heading", { name: /^Done: / })).toBeVisible();
  await fits(page, "the import's summary");
});

test("reflow: Administration at 320 px", async ({ page }) => {
  await newUser("reflow-admin-inv"); // a row with actions
  await logIn(page, await newUser("reflow-admin", { role: "ADMINISTRATOR" }));
  await page.goto("/admin");
  await expect(page.getByRole("table", { name: "Users" })).toBeVisible();
  await fits(page, "administration");
});

test.describe("on the market server", () => {
  test.use({ baseURL: MARKET });

  test("reflow: the price-sources page at 320 px, long names and its dialog", async ({ page }) => {
    await longNames(page); // a long unknown engine and long exchanges, in the answer only
    await openPriceSources(page, "reflow-sources");
    await fits(page, "the price-sources page");
    await page.getByRole("button", { name: "Remove Frankfurter" }).click();
    await expect(page.getByRole("dialog", { name: "Leave without a source?" })).toBeVisible();
    await fits(page, "the price-sources confirmation", { dialog: true });
  });

  test("reflow: search results and the create form at 320 px", async ({ page }) => {
    await logIn(page, await marketInvestor("reflow-search"));
    await apiPost(page, "/accounts", { name: "Reflow market", currency: "EUR" });
    await page.goto("/holdings/add");
    await page.getByLabel("Search by ISIN, name or symbol").fill("example text");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.getByText("1 result")).toBeVisible();
    await fits(page, "the search results");
    await page.locator(".search-result button").first().click();
    await expect(page.getByLabel("Name", { exact: true })).toBeVisible();
    await fits(page, "the create form");
  });
});

test("reflow: the screens without a session at 320 px", async ({ page }) => {
  await page.goto("/login");
  await heading(page, "Log in");
  await fits(page, "login", { bar: false });

  const temporary = await newUser("reflow-forced", { temporary: true });
  await page.getByLabel("Username").fill(temporary.username);
  await page.getByLabel("Password").fill(temporary.password);
  await page.getByRole("button", { name: "Log in" }).click();
  await heading(page, "Change your temporary password");
  await fits(page, "the forced password change", { bar: false });
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByLabel("Username")).toBeVisible();

  // The forced enrolment: `/me` answered in the browser with the obligation on (the real one
  // needs an empty server, two-factor.spec.ts); the enrolment's endpoints are real.
  const user = await newUser("reflow-2fa");
  await logIn(page, user);
  await fakeTwoFactorObligation(page);
  await page.goto("/");
  await heading(page, "Turn on two-step verification");
  await fits(page, "the forced enrolment", { bar: false });
  await enrolToQr(page, user.password);
  await fits(page, "the QR code", { bar: false });
  await page.unroute("**/api/v1/me");
  await page.context().clearCookies();

  // The first run: the setup status answered in the browser (the real one needs an empty
  // server, first-run.spec.ts); nothing is submitted.
  await fakeFirstRun(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Welcome", exact: true })).toBeVisible();
  await fits(page, "first run", { bar: false });
  await firstRunToLanguage(page);
  await fits(page, "first run, the language step", { bar: false });
});

test("reflow: the 2FA dialog and the login's code step at 320 px", async ({ page }) => {
  const user = await newUser("reflow-code");
  await logIn(page, user);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Turn on 2FA" }).click();
  const dialog = page.getByRole("dialog", { name: "Turn on two-step verification" });
  const secret = await enrolToQr(dialog, user.password);
  await fits(page, "the enrolment dialog's QR code", { dialog: true });
  await confirmEnrolment(dialog, secret);
  await fits(page, "the recovery codes in the dialog", { dialog: true });
  await dialog.getByRole("button", { name: "I saved the codes" }).click();
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Username").fill(user.username);
  await page.getByLabel("Password").fill(user.password);
  await page.getByRole("button", { name: "Log in" }).click();
  await heading(page, "Two-step verification");
  await fits(page, "the login's code step", { bar: false });
  await page.getByLabel("Code").fill(nextCode(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await heading(page, dashboardHeading(user));
});
