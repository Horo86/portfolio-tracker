import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";

import { routes } from "../router";
import { installSessionHandlers } from "../session";

/** The real routes and session handling at `path`, with a fresh cache and no retries. */
export function renderApp(path = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  installSessionHandlers(client);
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { client, router };
}

export const ME = {
  id: "00000000-0000-4000-8000-000000000001",
  username: "mario",
  display_name: "Mario",
  role: "INVESTOR",
  language: "en",
  theme: "moderno",
  reporting_currency: "EUR",
  must_change_password: false,
  two_factor_enabled: false,
  password_changed_at: "2026-07-01T10:00:00Z",
  last_login_at: "2026-10-18T09:00:00Z",
} as const;

export const NO_SESSION = { status: 401, body: { code: "NOT_AUTHENTICATED", detail: "x" } };

export const HEALTH = { body: { status: "ok", version: "0.1.0", database: "ok" } };
