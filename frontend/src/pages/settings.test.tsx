import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CSRF_HEADER, CSRF_VALUE } from "../api/client";
import type { Account, SessionInfo } from "../api/types";
import { type Handler, stubFetch } from "../test/fetchStub";
import { HEALTH, ME, NO_ACCOUNTS, renderApp } from "../test/renderApp";

const FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0";
const CHROME_WINDOWS =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
  "Chrome/141.0.0.0 Safari/537.36";

const ACCOUNT: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 0,
  currency_locked: false,
};

const session = (id: string, userAgent: string | null, current = false): SessionInfo => ({
  id: `00000000-0000-4000-8000-00000000050${id}`,
  created_at: "2026-10-18T12:00:00Z",
  last_used_at: "2026-10-20T12:00:00Z",
  expires_at: "2026-11-17T12:00:00Z",
  user_agent: userAgent,
  current,
});

function settings(extra: Record<string, Handler> = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": NO_ACCOUNTS,
    "GET /api/v1/currencies": { body: ["CHF", "EUR", "USD"] },
    "GET /api/v1/me/sessions": { body: [session("1", FIREFOX_LINUX, true)] },
    "PATCH /api/v1/me": ({ body }) => ({ body: { ...ME, ...(body as object) } }),
    ...extra,
  });
}

const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const patches = (calls: { method: string; body: unknown }[]) =>
  calls.filter((c) => c.method === "PATCH").map((c) => c.body);

describe("settings: appearance and currency", () => {
  it("changes the theme at once and saves it", async () => {
    const { calls } = settings();
    renderApp("/settings");
    expect(await screen.findByRole("heading", { name: "Settings", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Modern" })).toBeChecked();

    fireEvent.click(screen.getByRole("radio", { name: "Graphite" }));
    expect(document.documentElement.dataset.theme).toBe("grafite");
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Graphite" })).toBeChecked();
    expect(window.localStorage.getItem("pt.theme")).toBe("grafite");
    expect(patches(calls)).toEqual([{ theme: "grafite" }]);
    expect(calls.find((c) => c.method === "PATCH")?.headers.get(CSRF_HEADER)).toBe(CSRF_VALUE);
  });

  it("keeps the theme saved when an older /me answer arrives after it", async () => {
    let gets = 0;
    let late: (reply: { body: unknown }) => void = () => {};
    settings({
      "GET /api/v1/me": () =>
        ++gets === 1 ? { body: ME } : new Promise((resolve) => (late = resolve)),
    });
    renderApp("/settings");
    expect(await screen.findByRole("heading", { name: "Settings", level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(gets).toBe(2)); // the page's own refetch, on its way
    fireEvent.click(screen.getByRole("radio", { name: "Graphite" }));
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
    late({ body: ME }); // read before the change: still "moderno"
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByRole("radio", { name: "Graphite" })).toBeChecked();
  });

  it("puts the theme back when the server refuses it", async () => {
    settings({ "PATCH /api/v1/me": { status: 422, body: { code: "REQUEST_INVALID", detail: "x" } } });
    renderApp("/settings");
    fireEvent.click(await screen.findByRole("radio", { name: "Amber" }));
    expect(await screen.findByText(/^Some data is not valid\./)).toHaveAttribute("role", "alert");
    expect(document.documentElement.dataset.theme).toBe("moderno");
    expect(screen.getByRole("radio", { name: "Modern" })).toBeChecked();
  });

  it("switches the language and keeps it as the user's setting", async () => {
    const { calls } = settings();
    renderApp("/settings");
    fireEvent.change(await screen.findByLabelText("Language"), { target: { value: "it" } });
    expect(await screen.findByRole("heading", { name: "Impostazioni", level: 1 })).toBeInTheDocument();
    expect(await screen.findByText("Impostazioni salvate.")).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("it");
    expect(window.localStorage.getItem("pt.language")).toBe("it");
    expect(patches(calls)).toEqual([{ language: "it" }]);
  });

  it("changes the reporting currency, named in the user's language", async () => {
    const { calls } = settings();
    renderApp("/settings");
    const select = await screen.findByLabelText("Reporting currency");
    expect(await within(select).findByRole("option", { name: "CHF – Swiss Franc" })).toBeInTheDocument();
    expect(select).toHaveValue("EUR");
    const accountLoads = () => calls.filter((c) => c.path === "/api/v1/accounts").length;
    const before = accountLoads();

    fireEvent.change(select, { target: { value: "USD" } });
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
    expect(select).toHaveValue("USD");
    expect(patches(calls)).toEqual([{ reporting_currency: "USD" }]);
    // The accounts' values in the reporting currency are asked again.
    await screen.findByText("0 accounts · values in USD");
    expect(accountLoads()).toBeGreaterThan(before);
  });
});

describe("settings: security", () => {
  it("changes the password in a dialog", async () => {
    let attempts = 0;
    const { calls } = settings({
      "POST /api/v1/me/password": () =>
        ++attempts === 1
          ? { status: 422, body: { code: "PASSWORD_UNCHANGED", detail: "x" } }
          : { body: { ...ME, password_changed_at: "2026-10-20T12:00:00Z" } },
    });
    renderApp("/settings");
    expect(await screen.findByText("Last changed: Jul 1, 2026")).toBeInTheDocument();
    const open = screen.getByRole("button", { name: "Change password" });
    open.focus();
    fireEvent.click(open);

    const dialog = screen.getByRole("dialog", { name: "Change password" });
    expect(within(dialog).getByText(/After the change the other sessions/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Current password"), {
      target: { value: "my current password" },
    });
    fireEvent.change(within(dialog).getByLabelText("New password"), {
      target: { value: "my current password" },
    });
    fireEvent.change(within(dialog).getByLabelText("Repeat the new password"), {
      target: { value: "my current password" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(
      await within(dialog).findByText("The new password must be different from the current one."),
    ).toHaveAttribute("role", "alert");

    fireEvent.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(
      await screen.findByText("Password changed. The other sessions were logged out."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(open).toHaveFocus();
    expect(screen.getByText("Last changed: Oct 20, 2026")).toBeInTheDocument();
    const change = calls.filter((c) => c.method === "POST").at(-1);
    expect(change?.headers.get(CSRF_HEADER)).toBe(CSRF_VALUE);
  });

  it("lists the sessions and logs out the other devices", async () => {
    let open = [
      session("1", FIREFOX_LINUX, true),
      session("2", CHROME_WINDOWS),
      session("3", "custom-client/1.0"),
    ];
    const { calls } = settings({
      "GET /api/v1/me/sessions": () => ({ body: open }),
      "POST /api/v1/auth/logout-others": () => {
        open = open.filter((s) => s.current);
        return { body: { revoked: 2 } };
      },
    });
    renderApp("/settings");
    const list = await screen.findByRole("list", { name: "Active sessions" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Firefox on LinuxThis device");
    expect(items[1]).toHaveTextContent("Chrome on Windows");
    expect(items[2]).toHaveTextContent("custom-client/1.0");
    expect(items[0]).toHaveTextContent(/Logged in Oct 18, 2026, .* · last used Oct 20, 2026/);

    click("Log out from the other devices");
    expect(await screen.findByText("2 other sessions logged out.")).toBeInTheDocument();
    expect(await within(list).findAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Log out from the other devices" })).toBeDisabled();
    const post = calls.find((c) => c.path === "/api/v1/auth/logout-others");
    expect(post?.headers.get(CSRF_HEADER)).toBe(CSRF_VALUE);
  });
});

describe("settings: data and about", () => {
  it("downloads every transaction as CSV and links to About", async () => {
    settings();
    renderApp("/settings");
    const download = await screen.findByRole("link", { name: "Download CSV" });
    expect(download).toHaveAttribute("href", "/api/v1/transactions/export.csv");
    expect(download).toHaveAttribute("download");
    fireEvent.click(screen.getByRole("link", { name: "Open" }));
    expect(await screen.findByRole("heading", { name: "About", level: 1 })).toBeInTheDocument();
  });
});

describe("settings: focus, errors and notices", () => {
  it("changing the language keeps the focus on the language select", async () => {
    settings();
    renderApp("/settings");
    const select = await screen.findByLabelText("Language");
    select.focus();
    fireEvent.change(select, { target: { value: "it" } });
    expect(await screen.findByRole("heading", { name: "Impostazioni", level: 1 })).toBeInTheDocument();
    expect(screen.getByLabelText("Lingua")).toHaveFocus();
  });

  it("a new page takes the focus on its heading; the account in view does not", async () => {
    settings({ "GET /api/v1/accounts": { body: [ACCOUNT] } });
    renderApp("/settings");
    await screen.findByRole("heading", { name: "Settings", level: 1 });
    fireEvent.click(screen.getByRole("link", { name: "About" }));
    expect(await screen.findByRole("heading", { name: "About", level: 1 })).toHaveFocus();

    const selector = screen.getByLabelText("Account in view");
    await screen.findByRole("option", { name: "Conto titoli" });
    selector.focus();
    fireEvent.change(selector, { target: { value: ACCOUNT.id } });
    expect(selector).toHaveValue(ACCOUNT.id);
    expect(selector).toHaveFocus();
  });

  it("shows a refused reporting currency in its own section", async () => {
    settings({ "PATCH /api/v1/me": { status: 422, body: { code: "REQUEST_INVALID", detail: "x" } } });
    renderApp("/settings");
    const select = await screen.findByLabelText("Reporting currency");
    await within(select).findByRole("option", { name: /^USD/ });
    fireEvent.change(select, { target: { value: "USD" } });
    const alert = await screen.findByText(/^Some data is not valid\./);
    expect(alert.closest("section")).toHaveTextContent(/^Currency/);
  });

  it("announces the same confirmation again as a new message", async () => {
    settings();
    renderApp("/settings");
    fireEvent.click(await screen.findByRole("radio", { name: "Graphite" }));
    const first = await screen.findByText("Settings saved.");
    fireEvent.click(screen.getByRole("radio", { name: "Amber" }));
    await waitFor(() => expect(screen.getByText("Settings saved.")).not.toBe(first));
    expect(first).not.toBeInTheDocument();
  });

  it("keeps no password in the query cache after a change", async () => {
    settings({ "POST /api/v1/me/password": { body: ME } });
    const { client } = renderApp("/settings");
    fireEvent.click(await screen.findByRole("button", { name: "Change password" }));
    const dialog = screen.getByRole("dialog", { name: "Change password" });
    for (const [label, value] of [
      ["Current password", "my current password"],
      ["New password", "my brand new password"],
      ["Repeat the new password", "my brand new password"],
    ]) {
      fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
    }
    fireEvent.click(within(dialog).getByRole("button", { name: "Change password" }));
    await screen.findByText("Password changed. The other sessions were logged out.");
    const states = JSON.stringify(client.getMutationCache().getAll().map((m) => m.state));
    expect(states).not.toMatch(/my current password|my brand new password/);
  });
});
