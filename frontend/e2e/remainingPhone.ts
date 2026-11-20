// The remaining screens on phones and tablets (D-195): accounts, settings, about, the screens
// without a session and not found, shared by phone.spec.ts, tablet.spec.ts, reflow.spec.ts and
// a11y.spec.ts. Invented names and passwords only.
import type { Locator, Page } from "@playwright/test";

import { expect } from "./fixtures";
import { apiPost, logIn, newUser, nextCode, type User } from "./helpers";

/** A new investor with two accounts (one in USD, with a broker), logged in. */
export async function accountsInvestor(
  page: Page,
  prefix: string,
  { name = "Conto uno", provider = "Broker Esempio" } = {},
): Promise<User> {
  const user = await newUser(prefix);
  await logIn(page, user);
  const account = await apiPost<{ id: string }>(page, "/accounts", { name, currency: "EUR", provider });
  await apiPost(page, "/transactions", {
    account_id: account.id, type: "DEPOSIT", trade_date: "2025-01-02", total_amount: "123456789.00",
  }); // prettier-ignore
  await apiPost(page, "/accounts", { name: "Conto due", currency: "USD", provider: "Broker Esempio" });
  return user;
}

/** The boxes of the children of `scope` matched by `selector`, rounded. */
export async function boxes(scope: Locator, selector: string) {
  return scope.locator(selector).evaluateAll((list) =>
    list.map((el) => {
      const box = el.getBoundingClientRect();
      return {
        top: Math.round(box.top),
        left: Math.round(box.left),
        width: Math.round(box.width),
        right: Math.round(box.right),
      };
    }),
  );
}

/** The flex direction of each element matched by `selector` (a row stacks on phones). */
export async function directions(page: Page, selector: string): Promise<string[]> {
  return page
    .locator(selector)
    .evaluateAll((list) => list.map((el) => getComputedStyle(el).flexDirection));
}

/** Every button of the `.actions` rows inside `scope` takes the whole width of its row. */
export async function expectFullWidthButtons(scope: Locator, what: string): Promise<void> {
  const widths = await scope.locator(".actions .button").evaluateAll((list) =>
    list
      .filter((el) => (el as HTMLElement).offsetParent !== null)
      .map((el) => {
        const own = el.getBoundingClientRect();
        const row = el.parentElement!.getBoundingClientRect();
        return Math.round(own.width) === Math.round(row.width);
      }),
  );
  expect(widths.length, `buttons in ${what}`).toBeGreaterThan(0);
  expect(widths.every(Boolean), `every button of ${what} full width`).toBe(true);
}

/**
 * The first run, faked in the browser: the real one needs an empty server, used once per run
 * by first-run.spec.ts and two-factor.spec.ts. The setup status says the instance has no user,
 * so the welcome screen and its steps are drawn; nothing is ever submitted.
 */
export async function fakeFirstRun(page: Page): Promise<void> {
  await page.route("**/api/v1/setup/status", (route) =>
    route.fulfill({ json: { setup_required: true } }),
  );
}

/**
 * The forced enrolment, faked in the browser: the real one needs the obligation on (an empty
 * server, used by two-factor.spec.ts). `/me` says this user must enrol, so the guard shows the
 * screen; the enrolment's own endpoints stay real (any user may turn 2FA on).
 */
export async function fakeTwoFactorObligation(page: Page): Promise<void> {
  await page.route("**/api/v1/me", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const me = (await response.json()) as Record<string, unknown>;
    return route.fulfill({ response, json: { ...me, two_factor_setup_required: true } });
  });
}

/** Fills the welcome screen's first step (invented values) and goes to the language step. */
export async function firstRunToLanguage(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Start from scratch" }).click();
  await page.getByLabel("Username").fill("owner");
  await page.getByLabel("First name").fill("Olga");
  await page.getByLabel("Last name").fill("Esempio");
  await page.getByLabel("E-mail").fill("owner@example.org");
  await page.getByLabel("Password", { exact: true }).fill("first-run password");
  await page.getByLabel("Repeat the password").fill("first-run password");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("radio", { name: "English" })).toBeChecked();
}

/** On the enrolment (a dialog or the screen), the password, then the QR code: the secret. */
export async function enrolToQr(scope: Page | Locator, password: string): Promise<string> {
  await scope.getByLabel("Password").fill(password);
  await scope.getByRole("button", { name: "Continue" }).click();
  await expect(scope.getByRole("img", { name: "QR code for the authenticator app" })).toBeVisible();
  const key = (await scope.locator(".two-factor-key code").textContent()) ?? "";
  return key.replace(/\s/g, "");
}

/** Confirms the enrolment with a fresh code: the recovery codes are shown. */
export async function confirmEnrolment(scope: Page | Locator, secret: string): Promise<void> {
  await scope.getByLabel("6-digit code generated by the app").fill(nextCode(secret));
  await scope.getByRole("button", { name: "Confirm and turn on" }).click();
  await expect(scope.getByRole("list", { name: "Recovery codes" }).getByRole("listitem")).toHaveCount(10);
}

/** The QR code: 200 px, black on its white frame, wholly inside the window. */
export async function expectQrCode(page: Page): Promise<void> {
  const qr = await page.locator(".qr-frame").evaluate((frame) => {
    const code = frame.querySelector("svg")!.getBoundingClientRect();
    const box = frame.getBoundingClientRect();
    return {
      size: [Math.round(code.width), Math.round(code.height)],
      frame: getComputedStyle(frame).backgroundColor,
      inside: box.left >= 0 && box.right <= document.documentElement.clientWidth,
    };
  });
  expect(qr).toEqual({ size: [200, 200], frame: "rgb(255, 255, 255)", inside: true });
}

/** Each recovery code on a line of its own, inside the window. */
export async function expectCodesOnePerRow(page: Page): Promise<void> {
  const items = await boxes(page.getByRole("list", { name: "Recovery codes" }), "li");
  expect(items).toHaveLength(10);
  expect(new Set(items.map((item) => item.left)).size, "one column").toBe(1);
  const width = page.viewportSize()!.width;
  for (const item of items) expect(item.right).toBeLessThanOrEqual(width);
}

/** The five labels of the bottom bar, each on one line and inside its entry. */
export async function barLabels(page: Page) {
  return page.locator(".bottom-nav-item").evaluateAll((items) =>
    items.map((item) => {
      const range = document.createRange();
      const text = item.querySelector("span")!;
      range.selectNodeContents(text);
      const lines = new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size;
      const own = range.getBoundingClientRect();
      const entry = item.getBoundingClientRect();
      return {
        label: text.textContent ?? "",
        lines,
        inside: own.left >= entry.left - 0.5 && own.right <= entry.right + 0.5,
      };
    }),
  );
}
