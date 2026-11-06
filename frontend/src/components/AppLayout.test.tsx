import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CSRF_HEADER, CSRF_VALUE } from "../api/client";
import { type Handler, stubFetch } from "../test/fetchStub";
import { DASHBOARD, HEALTH, ME, NO_ACCOUNTS, NO_SESSION, renderApp } from "../test/renderApp";

function loggedIn(extra: Record<string, Handler> = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": NO_ACCOUNTS,
    "GET /api/v1/setup/status": { body: { setup_required: false } },
    ...extra,
  });
}

describe("app shell", () => {
  it("has a skip link, the main menu, the user and the version", async () => {
    loggedIn();
    renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });

    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    const menu = screen.getByRole("navigation", { name: "Main menu" });
    expect(within(menu).getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    // Pages of later slices and the administration (M6) are not in the menu yet.
    expect(within(menu).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Dashboard",
      "Accounts",
      "Holdings",
      "Transactions",
      "Import",
      "Settings",
    ]);
    expect(screen.getByText("Mario")).toBeInTheDocument();
    expect(screen.getByText("Investor")).toBeInTheDocument();
    // A 0.x version is a preview (D-167).
    expect(await screen.findByRole("link", { name: "About · v0.1.0 · preview" })).toBeInTheDocument();
  });

  it("logs out with the CSRF header and returns to the login", async () => {
    let loggedOut = false;
    const { calls } = loggedIn({
      "GET /api/v1/me": () => (loggedOut ? NO_SESSION : { body: ME }),
      "POST /api/v1/auth/logout": () => {
        loggedOut = true;
        return { status: 204 };
      },
    });
    const { client, router } = renderApp("/about");
    await screen.findByRole("heading", { name: "About" });

    fireEvent.click(screen.getByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    const logout = calls.find((c) => c.method === "POST");
    expect(logout?.headers.get(CSRF_HEADER)).toBe(CSRF_VALUE);
    expect(client.getQueryData(["me"])).toBeNull();
    // The next person to log in may be someone else: no page to return to.
    expect(router.state.location.state).toBeNull();
  });

  it("treats a session already gone on the server as logged out", async () => {
    // Expired or revoked elsewhere: /me answers 401 too.
    let logoutTried = false;
    loggedIn({
      "GET /api/v1/me": () => (logoutTried ? NO_SESSION : { body: ME }),
      "POST /api/v1/auth/logout": () => {
        logoutTried = true;
        return NO_SESSION;
      },
    });
    const { client } = renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(client.getQueryData(["me"])).toBeNull();
    await waitFor(() => expect(client.isFetching()).toBe(0));
    expect(screen.getByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("shows the About page: version, AGPL-3.0-only and the source code", async () => {
    loggedIn();
    renderApp("/about");
    expect(await screen.findByRole("heading", { name: "About" })).toBeInTheDocument();
    expect(await screen.findByText("0.1.0")).toBeInTheDocument();
    expect(screen.getByText("Development build")).toBeInTheDocument();
    expect(screen.getByText(/AGPL-3\.0-only/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "github.com/Horo86/portfolio-tracker" })).toHaveAttribute(
      "href",
      "https://github.com/Horo86/portfolio-tracker",
    );
  });

  it("shows a release's build date on About and no preview label (D-167)", async () => {
    loggedIn({
      "GET /api/health": {
        body: { ...HEALTH.body, version: "1.0.0", build_date: "2026-03-05T12:00:00Z", preview: false },
      },
    });
    renderApp("/about");
    expect(await screen.findByText("Built on Mar 5, 2026")).toBeInTheDocument();
    expect(screen.getByText("1.0.0")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "About · v1.0.0" })).toBeInTheDocument();
    expect(screen.queryByText(/preview/)).not.toBeInTheDocument();
  });

  it("says preview and the build date in Italian", async () => {
    loggedIn({
      "GET /api/v1/me": { body: { ...ME, language: "it" } },
      "GET /api/health": {
        body: { ...HEALTH.body, version: "0.9.0", build_date: "2026-03-05T12:00:00Z" },
      },
    });
    renderApp("/about");
    expect(await screen.findByText("Compilata il 5 mar 2026")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Informazioni · v0.9.0 · anteprima" })).toBeInTheDocument();
  });

  it("shows a not-found page inside the app for an unknown address", async () => {
    loggedIn();
    renderApp("/no/such/page");
    expect(await screen.findByRole("heading", { name: "Page not found", level: 1 })).toBeInTheDocument();
    expect(
      screen.getByText("The address does not exist or the security was deleted."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the dashboard" })).toHaveAttribute("href", "/");
    // The title is set by an effect, which may run after the heading is in the page.
    await waitFor(() => expect(document.title).toBe("Page not found · Portfolio Tracker"));
  });
});
