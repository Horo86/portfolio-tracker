import {
  administrationInTheMenu,
  focusRingOnTheBar,
  walkTheShell,
  wideContentStaysInside,
} from "./compactShell";
import { test } from "./fixtures";

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
});
