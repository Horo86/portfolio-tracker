import { type Browser, expect, type Page, test, watchCsp } from "./fixtures";

import { DASHBOARD, expectAccessible, logIn, logOut, newUser, type User } from "./helpers";

// The Users screen of the Administrator (D-165), on the seeded server, with invented users
// created for this test only. The user's own browser is a second context, guarded by hand.

async function usersScreen(page: Page) {
  await page.getByRole("link", { name: "Administration" }).click();
  await expect(page.getByRole("heading", { name: "Administration", level: 1 })).toBeVisible();
  await expect(page.getByRole("table", { name: "Users" })).toBeVisible();
}

/** Reads the temporary password from its dialog and closes it. */
async function temporaryFrom(page: Page, heading: string): Promise<string> {
  const dialog = page.getByRole("dialog", { name: heading });
  const password = (await dialog.locator("code").textContent()) ?? "";
  expect(password).toMatch(/^([A-Za-z0-9]{4}-){3}[A-Za-z0-9]{4}$/);
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(page.getByText(password)).toHaveCount(0); // shown once
  return password;
}

/** The user's own browser: log in with a temporary password and replace it. */
async function replaceTemporary(page: Page, user: User, temporary: string, notice: boolean) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(user.username);
  await page.getByLabel("Password").fill(temporary);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Change your temporary password" })).toBeVisible();
  const told = page.getByText(/^Your password was reset by the administrator on .+\.$/);
  await expect(told).toHaveCount(notice ? 1 : 0);
  await page.getByLabel("Temporary password").fill(temporary);
  await page.getByLabel("New password", { exact: true }).fill(user.password);
  await page.getByLabel("Repeat the new password").fill(user.password);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("heading", { name: DASHBOARD, level: 1 })).toBeVisible();
}

async function ownBrowser(browser: Browser) {
  const context = await browser.newContext({ locale: "en-US", timezoneId: "UTC" });
  const violations = await watchCsp(context);
  return { context, violations, page: await context.newPage() };
}

test("an investor has no Administration: not in the menu, and its address is not found", async ({
  page,
}) => {
  await logIn(page, await newUser("plain"));
  await expect(page.getByRole("link", { name: "Administration" })).toHaveCount(0);
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});

test("the administrator creates, resets, disables and enables a user", async ({
  page,
  browser,
}) => {
  const boss = await newUser("boss", { role: "ADMINISTRATOR" });
  await logIn(page, boss);
  await usersScreen(page);
  const own = page.getByRole("row").filter({ has: page.getByRole("rowheader", { name: boss.username }) });
  await expect(own.getByText("You")).toBeVisible();
  await expect(own.getByRole("button")).toHaveCount(0);
  await expectAccessible(page, "the Users screen");

  // Create: an investor by default; the temporary password once.
  const user = { username: `giulia-${Date.now().toString(36)}`, password: "giulia e2e password" };
  await page.getByRole("button", { name: "New user" }).click();
  const form = page.getByRole("dialog", { name: "New user" });
  await expect(form.getByLabel("Role")).toHaveValue("INVESTOR");
  await form.getByLabel("Username").fill(user.username);
  await expectAccessible(page, "the New user dialog");
  await form.getByRole("button", { name: "Create user" }).click();
  const first = await temporaryFrom(page, "User created");
  await expect(page.getByText(`User ${user.username} created.`)).toBeVisible();
  const row = page.getByRole("row").filter({
    has: page.getByRole("rowheader", { name: user.username }),
  });
  await expect(row.getByText("Temporary password")).toBeVisible();

  const theirs = await ownBrowser(browser);
  await replaceTemporary(theirs.page, user, first, false); // a new user: no reset notice

  // Reset: the user's session ends; at the next login they are told, with the date.
  await row.getByRole("button", { name: `Reset the password of ${user.username}` }).click();
  await page
    .getByRole("dialog", { name: `Reset the password of ${user.username}?` })
    .getByRole("button", { name: "Reset password" })
    .click();
  const second = await temporaryFrom(page, "Password reset");
  await theirs.page.reload();
  await expect(theirs.page.getByLabel("Username")).toBeVisible(); // logged out
  await replaceTemporary(theirs.page, user, second, true);
  await logOut(theirs.page);
  await logIn(theirs.page, user); // the notice is gone with the temporary password

  // Disable: the session ends and the login is refused; enable: the login works again.
  await row.getByRole("button", { name: `Disable ${user.username}` }).click();
  await page
    .getByRole("dialog", { name: `Disable ${user.username}?` })
    .getByRole("button", { name: "Disable" })
    .click();
  await expect(page.getByText(`User ${user.username} disabled.`)).toBeVisible();
  await expect(row.getByText("Disabled")).toBeVisible();
  await theirs.page.reload();
  await expect(theirs.page.getByLabel("Username")).toBeVisible();
  await theirs.page.getByLabel("Username").fill(user.username);
  await theirs.page.getByLabel("Password").fill(user.password);
  await theirs.page.getByRole("button", { name: "Log in" }).click();
  await expect(theirs.page.getByText("Wrong username or password.")).toBeVisible();

  await row.getByRole("button", { name: `Enable ${user.username}` }).click();
  await expect(page.getByText(`User ${user.username} enabled again.`)).toBeVisible();
  await expect(row.getByText("Active")).toBeVisible();
  await logIn(theirs.page, user);

  expect(theirs.violations).toEqual([]);
  await theirs.context.close();
});
