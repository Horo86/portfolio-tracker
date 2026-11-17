import { expect, test, watchCsp } from "./fixtures";

import { DEMO, logIn, logOut, newUser } from "./helpers";

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
  const violations = await watchCsp(other); // a context of its own: guarded by hand
  const second = await other.newPage();
  await logIn(second, user);
  await expect(second.locator("html")).toHaveAttribute("data-theme", "ambra");
  await expect(second.locator("html")).toHaveAttribute("lang", "it");
  expect(violations).toEqual([]);
  await other.close();
});

test("light or dark: per browser, kept after reload and logout, same as the device by default", async ({
  page,
}) => {
  const html = page.locator("html");
  const user = await newUser("dark");
  await logIn(page, user);
  await page.getByRole("link", { name: "Settings" }).click();
  const group = page.getByRole("group", { name: "Light or dark" });
  await expect(group.getByRole("radio", { name: "Same as the device" })).toBeChecked();
  await expect(html).toHaveAttribute("data-theme", "moderno");

  // Dark chosen: at once, after a reload, and on the login page after logging out.
  await group.getByRole("radio", { name: "Dark" }).check();
  await expect(html).toHaveAttribute("data-theme", "scuro");
  await expect(page.getByText("The app colours apply with the light appearance.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  await expect(html).toHaveAttribute("data-theme", "scuro");
  await expect(page.getByRole("radio", { name: "Dark" })).toBeChecked();
  await logOut(page);
  await expect(html).toHaveAttribute("data-theme", "scuro");
  const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(background).toBe("rgb(15, 17, 23)");

  // Same as the device: dark on a dark device, the colour theme on a light one.
  await logIn(page, user);
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("radio", { name: "Same as the device" }).check();
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(html).toHaveAttribute("data-theme", "scuro");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  await expect(html).toHaveAttribute("data-theme", "scuro");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(html).toHaveAttribute("data-theme", "moderno");
  await expect(page.getByText("The app colours apply with the light appearance.")).toHaveCount(0);
});

test("the dashboard's charts redraw in dark when the device switches", async ({ page }) => {
  await logIn(page, DEMO);
  const slice = page.locator(".chart-donut svg path[fill^='#']").first();
  await expect(slice).toBeVisible();
  const light = (await slice.getAttribute("fill"))?.toLowerCase();
  expect(light).not.toBe("#818cf8");

  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "scuro");
  const chart1 = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--chart-1").trim(),
  );
  expect(chart1).toBe("#818cf8");
  await expect(slice).toHaveAttribute("fill", new RegExp(`^${chart1}$`, "i"));
});

// After an update a tab may ask for a language chunk that is no longer on the server.
const ITALIAN_TEXTS = /\/assets\/it-[\w-]+\.js$/;

test("the active language's texts cannot be loaded: the other language is used", async ({
  browser,
}) => {
  const context = await browser.newContext({ locale: "it-IT" });
  const violations = await watchCsp(context);
  const page = await context.newPage();
  await page.route(ITALIAN_TEXTS, (route) => route.abort());
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Log in", level: 1 })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  expect(violations).toEqual([]);
  await context.close();
});

test("a language whose texts cannot be loaded: the current one stays, no error", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await logIn(page, await newUser("language-gone"));
  await page.route(ITALIAN_TEXTS, (route) => route.abort());
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByLabel("Language").selectOption("it");
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  expect(errors).toEqual([]);
});

test("log out from the other devices", async ({ page, browser }) => {
  const user = await newUser("devices");
  const other = await browser.newContext();
  const violations = await watchCsp(other);
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
  await elsewhere.getByRole("link", { name: "Accounts", exact: true }).click();
  await expect(elsewhere.getByRole("heading", { name: "Log in" })).toBeVisible();
  // This one still works.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  expect(violations).toEqual([]);
  await other.close();
});
