import { administrationInTheMenu, walkTheShell } from "./compactShell";
import { expect, test } from "./fixtures";
import { logIn, newUser } from "./helpers";

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
