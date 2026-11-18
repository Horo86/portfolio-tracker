import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubFetch } from "../test/fetchStub";
import { DASHBOARD, HEALTH, ME, NO_ACCOUNTS, NO_SESSION, renderApp } from "../test/renderApp";
import { COMPACT_QUERY } from "../useCompact";

function loggedIn(me: object = ME) {
  let loggedOut = false;
  const stub = stubFetch({
    "GET /api/v1/me": () => (loggedOut ? NO_SESSION : { body: me }),
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": NO_ACCOUNTS,
    "GET /api/v1/setup/status": { body: { setup_required: false } },
    "POST /api/v1/auth/logout": () => {
      loggedOut = true;
      return { status: 204 };
    },
  });
  return stub;
}

describe("compact shell (D-189)", () => {
  beforeEach(() => {
    // A window narrower than 1024 px.
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === COMPACT_QUERY,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows a top strip and a bar of five entries, no sidebar", async () => {
    loggedIn();
    renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });

    expect(screen.getByRole("banner")).toHaveTextContent("Account in view");
    expect(screen.getByRole("combobox", { name: "Account in view" })).toBeInTheDocument();
    const bar = screen.getByRole("navigation", { name: "Main menu" });
    const entries = within(bar).getAllByRole("listitem").map((item) => item.textContent);
    expect(entries).toEqual(["Dashboard", "Holdings", "Transactions", "Import", "More"]);
    expect(within(bar).getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    expect(within(bar).getByRole("button", { name: "More" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    // Skip link, header, main, nav: the order of the page.
    const order = [...document.querySelectorAll(".shell > *")].map((el) => el.tagName);
    expect(order).toEqual(["A", "HEADER", "MAIN", "NAV"]);
  });

  it("opens Altro with the other pages, the user and log out; no Administration for an investor", async () => {
    loggedIn();
    renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });
    fireEvent.click(screen.getByRole("button", { name: "More" }));

    const menu = await screen.findByRole("dialog", { name: "More" });
    const pages = within(menu).getByRole("list", { name: "More pages" });
    await within(pages).findByRole("link", { name: "About · v0.1.0 · preview" });
    expect(within(pages).getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Accounts",
      "Settings",
      "About · v0.1.0 · preview",
    ]);
    expect(within(menu).getByText("Mario Esempio")).toBeInTheDocument();
    expect(within(menu).getByText("Investor")).toBeInTheDocument();
    expect(within(menu).getByRole("button", { name: "Log out" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More" })).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(within(menu).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lists Administration for an administrator", async () => {
    loggedIn({ ...ME, role: "ADMINISTRATOR" });
    renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const menu = await screen.findByRole("dialog", { name: "More" });
    expect(within(menu).getByRole("link", { name: "Administration" })).toHaveAttribute("href", "/admin");
  });

  it("goes to a page of the menu and closes it", async () => {
    loggedIn();
    renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const menu = await screen.findByRole("dialog", { name: "More" });
    fireEvent.click(within(menu).getByRole("link", { name: "Accounts" }));
    expect(await screen.findByRole("heading", { name: "Accounts", level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("logs out from the menu", async () => {
    loggedIn();
    renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const menu = await screen.findByRole("dialog", { name: "More" });
    fireEvent.click(within(menu).getByRole("button", { name: "Log out" }));
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });
});

describe("desktop shell", () => {
  it("keeps the sidebar where matchMedia is missing (jsdom, old browsers)", async () => {
    loggedIn();
    renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });
    expect(screen.getByRole("complementary")).toBeInTheDocument();
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More" })).not.toBeInTheDocument();
  });
});
