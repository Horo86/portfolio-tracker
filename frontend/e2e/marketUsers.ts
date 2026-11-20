import { type APIRequestContext, expect, request } from "@playwright/test";

import { MARKET } from "../playwright.config";
import { CSRF, NEW_USER_NAMES, PASSWORD, type User } from "./helpers";

// The users of the market server (:8712, invented fakes, D-151). Its folder is fresh at each
// run and any spec may reach it first, alone or in the full suite, in any order: whoever comes
// first creates the administrator, anyone after logs in with the same invented credentials.

export const MARKET_ADMIN: User = {
  username: "market-admin",
  password: "e2e market password 0001",
  first_name: "Marco",
  last_name: "Esempio",
};

let serial = 0;

async function apiAs(user: Pick<User, "username" | "password">): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL: MARKET, extraHTTPHeaders: CSRF });
  const login = await api.post("/api/v1/auth/login", {
    data: { username: user.username, password: user.password },
  });
  expect(login.ok(), `login as ${user.username} on the market server`).toBe(true);
  return api;
}

async function done(api: APIRequestContext): Promise<void> {
  await api.post("/api/v1/auth/logout");
  await api.dispose();
}

/** The market server's administrator: created when the server has no user yet (201), else
 * already there (409 SETUP_ALREADY_DONE) and able to log in. */
export async function ensureMarketAdmin(): Promise<void> {
  const api = await request.newContext({ baseURL: MARKET, extraHTTPHeaders: CSRF });
  const created = await api.post("/api/v1/setup/admin", {
    data: { ...MARKET_ADMIN, email: "market-admin@example.org", language: "en" },
  });
  await api.dispose();
  expect([201, 409], "the market server's administrator").toContain(created.status());
  await done(await apiAs(MARKET_ADMIN));
}

/** A new investor on the market server, with its own search and price-check limits (D-128,
 * D-178). */
export async function marketInvestor(prefix: string): Promise<User> {
  await ensureMarketAdmin();
  const admin = await apiAs(MARKET_ADMIN);
  const username = `${prefix}-${Date.now().toString(36)}${serial++}`;
  const created = await admin.post("/api/v1/admin/users", {
    data: {
      username,
      language: "en",
      role: "INVESTOR",
      ...NEW_USER_NAMES,
      email: `${username}@example.org`,
    },
  });
  expect(created.ok(), "create a market investor").toBe(true);
  const { temporary_password } = (await created.json()) as { temporary_password: string };
  await done(admin);
  const user = await apiAs({ username, password: temporary_password });
  const changed = await user.post("/api/v1/me/password", {
    data: { current_password: temporary_password, new_password: PASSWORD },
  });
  expect(changed.ok(), "replace the temporary password").toBe(true);
  await done(user);
  return { username, password: PASSWORD, ...NEW_USER_NAMES };
}
