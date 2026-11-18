import { administrationInTheMenu, walkTheShell } from "./compactShell";
import { test } from "./fixtures";

// A tablet in portrait (D-189): 820x1180, the desktop page with the compact shell.
test.use({ viewport: { width: 820, height: 1180 } });

test("tablet: the bar, the menu, the account in view and log out", async ({ page }) => {
  await walkTheShell(page, "tablet");
});

test("tablet: an administrator reaches Administration from the menu", async ({ page }) => {
  await administrationInTheMenu(page, "tablet-admin");
});
