import { expect, test } from "@playwright/test";

import { logIn, newUser } from "./helpers";

test("theme and language are the user's: kept after reload and on another browser", async ({
  page,
  browser,
}) => {
  // The texts of each language are a chunk of their own: only the active one loads.
  const texts: string[] = [];
  page.on("request", (request) => {
    const chunk = /\/assets\/(it|en)-[\w-]+\.js$/.exec(request.url());
    if (chunk) texts.push(chunk[1]);
  });
  const user = await newUser("prefs");
  await logIn(page, user);
  expect(texts).toEqual(["en"]);
  await page.getByRole("link", { name: "Settings" }).click();
  await page.locator("label", { hasText: "Amber" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await page.getByLabel("Language").selectOption("it");
  await expect(page.getByText("Impostazioni salvate.")).toBeVisible();
  expect(texts).toEqual(["en", "it"]);

  texts.length = 0;
  await page.reload();
  await expect(page.getByRole("heading", { name: "Impostazioni", level: 1 })).toBeVisible();
  expect(texts).toEqual(["it"]);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "ambra");
  await expect(page.getByRole("radio", { name: "Ambra" })).toBeChecked();

  // A browser that never saw them: after login they come from the server (Q-7).
  const other = await browser.newContext();
  const second = await other.newPage();
  await logIn(second, user);
  await expect(second.locator("html")).toHaveAttribute("data-theme", "ambra");
  await expect(second.locator("html")).toHaveAttribute("lang", "it");
  await other.close();
});

test("log out from the other devices", async ({ page, browser }) => {
  const user = await newUser("devices");
  const other = await browser.newContext();
  const elsewhere = await other.newPage();
  await logIn(elsewhere, user);
  await logIn(page, user);

  await page.getByRole("link", { name: "Settings" }).click();
  const sessions = page.getByRole("list", { name: "Active sessions" });
  await expect(sessions.getByRole("listitem")).toHaveCount(2);
  // The "Desktop Chrome" device of the configuration sends a Windows user agent.
  await expect(sessions.getByRole("listitem").first()).toContainText("Chrome on Windows");
  await expect(sessions.getByText("This device")).toHaveCount(1);

  await page.getByRole("button", { name: "Log out from the other devices" }).click();
  await expect(page.getByText("1 other session logged out.")).toBeVisible();
  await expect(sessions.getByRole("listitem")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Log out from the other devices" })).toBeDisabled();

  // The other browser finds its session gone at its next request.
  await elsewhere.getByRole("link", { name: "Accounts" }).click();
  await expect(elsewhere.getByRole("heading", { name: "Log in" })).toBeVisible();
  // This one still works.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  await other.close();
});
