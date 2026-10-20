import { expect, test } from "@playwright/test";

import { DEMO, expectAccessible, logIn, newUser } from "./helpers";

test("a wrong password is explained without the server's detail", async ({ page }) => {
  await page.goto("/login");
  await expectAccessible(page, "login");
  await page.getByLabel("Username").fill(DEMO.username);
  await page.getByLabel("Password").fill("not the password");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Wrong username or password.");
  await expect(page.getByLabel("Password")).toHaveValue("");
});

test("too many attempts: the login says how long to wait (429)", async ({ page }) => {
  await page.goto("/login");
  // An unknown username: its own throttle key, so no real user is blocked.
  await page.getByLabel("Username").fill("nobody-e2e");
  const alert = page.getByRole("alert");
  // Five failures are free (D-82); the sixth is refused for a minute.
  for (let attempt = 1; attempt <= 6; attempt++) {
    await page.getByLabel("Password").fill(`wrong ${attempt}`);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page.getByRole("button", { name: "Log in" })).toBeEnabled();
  }
  await expect(alert).toHaveText("Too many attempts. Try again in 1 minute.");
});

test("a temporary password must be changed before anything else", async ({ page }) => {
  const user = await newUser("newcomer", { temporary: true });
  await page.goto("/login");
  await page.getByLabel("Username").fill(user.username);
  await page.getByLabel("Password").fill(user.password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Change your temporary password" })).toBeVisible();
  await expectAccessible(page, "forced password change");

  // No other page is reachable meanwhile.
  await page.goto("/accounts");
  await expect(page.getByRole("heading", { name: "Change your temporary password" })).toBeVisible();

  await page.getByLabel("Temporary password").fill(user.password);
  await page.getByLabel("New password", { exact: true }).fill("my own new password");
  await page.getByLabel("Repeat the new password").fill("my own new password");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

  await page.getByRole("button", { name: "Log out", exact: true }).click();
  await logIn(page, { username: user.username, password: "my own new password" });
});
