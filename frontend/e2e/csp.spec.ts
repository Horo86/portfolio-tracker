import { test as plain } from "@playwright/test";

import { EMPTY } from "../playwright.config";
import { expect, watchCsp } from "./fixtures";

// The policy of D-163 reaches the browser and the guard of `fixtures.ts` sees what it blocks.
// This spec uses Playwright's own `test`: it causes a violation on purpose.
const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; " +
  "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; " +
  "form-action 'self'; frame-ancestors 'none'";

plain("the page carries the strict policy and the guard reports an inline script", async ({
  browser,
}) => {
  const context = await browser.newContext({ baseURL: EMPTY });
  const violations = await watchCsp(context);
  const page = await context.newPage();
  const response = await page.goto("/");
  expect(response?.headers()["content-security-policy"]).toBe(CSP);
  expect(response?.headers()["strict-transport-security"]).toBeUndefined();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(violations).toEqual([]); // the app itself breaks nothing

  await page.evaluate(() => {
    const script = document.createElement("script");
    script.textContent = "document.title = 'inline ran'";
    document.head.append(script);
  });
  await expect.poll(() => violations.length).toBeGreaterThan(0);
  expect(await page.title()).not.toBe("inline ran");
  await context.close();
});
