import AxeBuilder from "@axe-core/playwright";
import { type APIRequestContext, expect, type Page, request } from "@playwright/test";

import { SEEDED } from "../playwright.config";

// The development data's users (backend/scripts/dev_data.py): public, invented credentials
// and names of a throwaway data directory.
export const ADMIN: User = {
  username: "admin",
  password: "dev-admin-password",
  first_name: "Ada",
  last_name: "Esempio",
};
export const DEMO: User = {
  username: "demo",
  password: "dev-demo-password",
  first_name: "Demo",
  last_name: "Esempio",
};
export const CSRF = { "X-Requested-With": "portfolio-tracker" };
export const PASSWORD = "e2e password 0001";
// The names every user made by `newUser` gets (letters only, as D-173 asks).
export const NEW_USER_NAMES = { first_name: "Prova", last_name: "Utente" };

export type User = { username: string; password: string; first_name: string; last_name: string };

let serial = 0;

async function apiAs(user: User): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL: SEEDED, extraHTTPHeaders: CSRF });
  const login = await api.post("/api/v1/auth/login", {
    data: { username: user.username, password: user.password },
  });
  expect(login.ok(), `login as ${user.username}`).toBe(true);
  return api;
}

/** Ends the API session, so it does not show among the user's sessions. */
async function done(api: APIRequestContext): Promise<void> {
  await api.post("/api/v1/auth/logout");
  await api.dispose();
}

/** A new user (an investor unless `role` says otherwise) created by the Administrator through
 * the API, a quick way to prepare a test. With `temporary`, its temporary password is returned
 * unchanged. */
export async function newUser(
  prefix: string,
  {
    temporary = false,
    language = "en",
    role = "INVESTOR",
    names = NEW_USER_NAMES,
    email = "",
  } = {},
): Promise<User> {
  const admin = await apiAs(ADMIN);
  const username = `${prefix}-${Date.now().toString(36)}${serial++}`;
  const created = await admin.post("/api/v1/admin/users", {
    data: { username, language, role, ...names, email: email || `${username}@example.org` },
  });
  expect(created.ok(), "create user").toBe(true);
  const { temporary_password } = (await created.json()) as { temporary_password: string };
  await done(admin);
  if (temporary) return { username, password: temporary_password, ...names };

  const user = await apiAs({ username, password: temporary_password, ...names });
  const changed = await user.post("/api/v1/me/password", {
    data: { current_password: temporary_password, new_password: PASSWORD },
  });
  expect(changed.ok(), "replace the temporary password").toBe(true);
  await done(user);
  return { username, password: PASSWORD, ...names };
}

/** The dashboard's heading: it greets this user by first and last name (D-153, D-173), in
 * either language (a user's own language wins after the login). */
export function dashboardHeading(user: Pick<User, "first_name" | "last_name">): RegExp {
  const name = `${user.first_name} ${user.last_name}`.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^(Welcome|Benvenuto) ${name}$`);
}

/** Logs in through the login page and waits for the dashboard. */
export async function logIn(page: Page, user: User): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Username").fill(user.username);
  await page.getByLabel("Password").fill(user.password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: dashboardHeading(user), level: 1 })).toBeVisible();
}

/** Logs out with the sidebar's button and waits for the login form: the session ends only
 * when the server has answered, and a navigation before that would cancel the logout
 * (CI run 40691641866). */
export async function logOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Log out", exact: true }).click();
  await expect(page.getByLabel("Username")).toBeVisible();
}

/** A write through the page's own session (cookies), e.g. to prepare data quickly. */
export async function apiPost<T>(page: Page, path: string, data: unknown): Promise<T> {
  const response = await page.request.post(`/api/v1${path}`, { data, headers: CSRF });
  expect(response.ok(), `POST ${path}: ${response.status()}`).toBe(true);
  return (await response.json()) as T;
}

export async function apiPatch(page: Page, path: string, data: unknown): Promise<void> {
  const response = await page.request.patch(`/api/v1${path}`, { data, headers: CSRF });
  expect(response.ok(), `PATCH ${path}: ${response.status()}`).toBe(true);
}

/**
 * No WCAG 2.1 A/AA violation found by axe on the page as it is now, and no form inside another
 * one: not valid HTML, and there the browser submits the inner form natively, reloading the page.
 */
export async function expectAccessible(page: Page, what: string): Promise<void> {
  expect(await page.locator("form form").count(), `a form inside a form on ${what}`).toBe(0);
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const found = results.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
  );
  expect(found, `axe on ${what}`).toEqual([]);
}
