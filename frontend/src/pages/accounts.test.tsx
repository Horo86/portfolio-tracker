import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CSRF_HEADER, CSRF_VALUE } from "../api/client";
import type { Account, AccountSummary } from "../api/types";
import { type Handler, stubFetch } from "../test/fetchStub";
import { HEALTH, ME, renderApp } from "../test/renderApp";

// Invented accounts and figures only.
const SECURITIES: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: "Broker Esempio",
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 14,
  currency_locked: true,
};

const FOREIGN: Account = {
  id: "00000000-0000-4000-8000-0000000000a2",
  name: "Conto estero",
  provider: null,
  currency: "USD",
  external_code: null,
  created_at: "2026-02-10T09:00:00Z",
  transaction_count: 0,
  currency_locked: false,
};

const summary = (account: Account, figures: Partial<AccountSummary>): AccountSummary => ({
  account_id: account.id,
  currency: account.currency,
  day: "2026-10-20",
  total_value: "0",
  securities_value: "0",
  cash: "0",
  invested_capital: "0",
  gain_gross: "0",
  gain_net: "0",
  partial: false,
  open_positions: 0,
  stale_fx: [],
  reporting: { currency: "EUR", total_value: "0", stale_fx: null },
  ...figures,
});

const SECURITIES_SUMMARY = summary(SECURITIES, {
  total_value: "21368.05",
  cash: "1240.1",
  gain_net: "4980.22",
  open_positions: 5,
  reporting: { currency: "EUR", total_value: "21368.05", stale_fx: null },
});

const FOREIGN_SUMMARY = summary(FOREIGN, {
  total_value: "8420",
  cash: "312.4",
  gain_net: "-410.15",
  open_positions: 2,
  partial: true,
  stale_fx: [{ from_currency: "GBP", to_currency: "USD", rate_date: "2026-10-09" }],
  reporting: {
    currency: "EUR",
    total_value: "7771.9",
    stale_fx: { from_currency: "USD", to_currency: "EUR", rate_date: "2026-10-09" },
  },
});

function withAccounts(list: Account[], extra: Record<string, Handler> = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": () => ({ body: list }),
    "GET /api/v1/currencies": { body: ["CHF", "EUR", "USD"] },
    [`GET /api/v1/accounts/${SECURITIES.id}/summary`]: { body: SECURITIES_SUMMARY },
    [`GET /api/v1/accounts/${FOREIGN.id}/summary`]: { body: FOREIGN_SUMMARY },
    ...extra,
  });
}

const card = (name: string) => screen.getByRole("region", { name });
const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

describe("accounts page", () => {
  it("shows each account's figures in its currency, with partial and stale-rate notices", async () => {
    withAccounts([SECURITIES, FOREIGN]);
    renderApp("/accounts");
    expect(await screen.findByRole("heading", { name: "Accounts", level: 1 })).toBeInTheDocument();
    expect(await screen.findByText(/^2 accounts · “Open” sets/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Accounts" })).toHaveAttribute("aria-current", "page");

    const securities = card("Conto titoli");
    expect(await within(securities).findByText("€21,368.05")).toBeInTheDocument();
    expect(within(securities).getByText("€1,240.10")).toBeInTheDocument();
    expect(within(securities).getByText("+€4,980.22")).toHaveClass("positive");
    expect(within(securities).getByText("Broker Esempio")).toBeInTheDocument();
    expect(within(securities).getByText("Values in EUR")).toBeInTheDocument();
    expect(within(securities).queryByText(/Value in EUR/)).toBeNull();

    const foreign = card("Conto estero");
    expect(await within(foreign).findByText("-$410.15")).toHaveClass("negative");
    expect(within(foreign).getByText("Values in USD, also shown in EUR")).toBeInTheDocument();
    expect(within(foreign).getByText("Partial")).toBeInTheDocument();
    expect(within(foreign).getByText(/an exchange rate is missing/)).toBeInTheDocument();
    expect(
      within(foreign).getByText(
        "The GBP/USD exchange rate is from Oct 9, 2026: it has not been updated recently.",
      ),
    ).toBeInTheDocument();
    expect(
      within(foreign).getByText(
        "Value in EUR at the rate of Oct 9, 2026, not updated recently: €7,771.90",
      ),
    ).toBeInTheDocument();
  });

  it("says when the value in the reporting currency is missing", async () => {
    withAccounts([FOREIGN], {
      [`GET /api/v1/accounts/${FOREIGN.id}/summary`]: {
        body: { ...FOREIGN_SUMMARY, reporting: { currency: "EUR", total_value: null, stale_fx: null } },
      },
    });
    renderApp("/accounts");
    expect(
      await screen.findByText("Value in EUR not available: the exchange rate is missing."),
    ).toBeInTheDocument();
  });

  it("“Open” sets the account in view, which every link then keeps", async () => {
    withAccounts([SECURITIES, FOREIGN]);
    const { router } = renderApp("/accounts");
    await within(await screen.findByRole("region", { name: "Conto estero" })).findByText("$8,420.00");

    fireEvent.click(within(card("Conto estero")).getByRole("link", { name: "Open" }));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(router.state.location.search).toBe(`?account=${FOREIGN.id}`);
    const selector = screen.getByLabelText("Account in view");
    expect(selector).toHaveValue(FOREIGN.id);
    expect(selector).toHaveAccessibleDescription("Values in USD");
    expect(screen.getByRole("link", { name: "Accounts" })).toHaveAttribute(
      "href",
      `/accounts?account=${FOREIGN.id}`,
    );

    fireEvent.change(selector, { target: { value: "" } });
    expect(router.state.location.search).toBe("");
    expect(selector).toHaveAccessibleDescription("2 accounts · values in EUR");
    expect(screen.getByRole("link", { name: "Accounts" })).toHaveAttribute("href", "/accounts");
  });

  it("treats an unknown account in the address as all accounts", async () => {
    withAccounts([SECURITIES]);
    renderApp("/?account=00000000-0000-4000-8000-0000000000ff");
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(await screen.findByRole("option", { name: "Conto titoli" })).toBeInTheDocument();
    expect(screen.getByLabelText("Account in view")).toHaveValue("");
    expect(screen.getByRole("link", { name: "Accounts" })).toHaveAttribute("href", "/accounts");
  });
});

describe("account dialogs", () => {
  it("creates the first account: name required, reporting currency proposed", async () => {
    const list: Account[] = [];
    const { calls } = withAccounts(list, {
      "POST /api/v1/accounts": ({ body }) => {
        const created = { ...FOREIGN, ...(body as object), provider: "Broker Esempio" };
        list.push(created);
        return { status: 201, body: created };
      },
    });
    renderApp("/accounts");
    expect(await screen.findByRole("heading", { name: "No accounts yet" })).toBeInTheDocument();
    click("Create the first account");

    const dialog = screen.getByRole("dialog", { name: "New account" });
    expect(within(dialog).getByText(/cannot be changed after the first transaction/)).toBeInTheDocument();
    expect(await within(dialog).findByRole("option", { name: "USD – US Dollar" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Currency")).toHaveValue("EUR");

    click("Create account");
    expect(within(dialog).getByText("Fill in this field.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Name")).toHaveFocus();

    type("Name", "  Conto estero ");
    type("Currency", "USD");
    type("Broker (optional)", "Broker Esempio");
    click("Create account");

    expect(await screen.findByText("Account “Conto estero” created.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await screen.findByRole("region", { name: "Conto estero" })).toBeInTheDocument();
    const create = calls.find((c) => c.method === "POST");
    expect(create?.body).toEqual({ name: "Conto estero", currency: "USD", provider: "Broker Esempio" });
    expect(create?.headers.get(CSRF_HEADER)).toBe(CSRF_VALUE);
  });

  it("edits an account; its currency is fixed once it has transactions", async () => {
    let attempts = 0;
    const { calls } = withAccounts([SECURITIES], {
      [`PATCH /api/v1/accounts/${SECURITIES.id}`]: ({ body }) =>
        ++attempts === 1
          ? { status: 422, body: { code: "REQUEST_INVALID", detail: "name: too long" } }
          : { body: { ...SECURITIES, ...(body as object) } },
    });
    renderApp("/accounts");
    const edit = within(await screen.findByRole("region", { name: "Conto titoli" })).getByRole(
      "button",
      { name: "Edit" },
    );
    edit.focus();
    fireEvent.click(edit);

    const dialog = screen.getByRole("dialog", { name: "Edit account" });
    const currency = within(dialog).getByLabelText("Currency");
    expect(currency).toBeDisabled();
    expect(currency).toHaveAccessibleDescription("Fixed: the account already has transactions.");

    type("Name", "Conto principale");
    type("Account code at the broker (optional)", "ZZ-0001");
    click("Save");
    expect(
      await within(dialog).findByText("Some data is not valid. Check the fields and try again."),
    ).toHaveAttribute("role", "alert");
    expect(screen.queryByText(/too long/)).toBeNull();

    click("Save");
    expect(await screen.findByText("Account “Conto principale” saved.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({
      name: "Conto principale",
      provider: "Broker Esempio",
      external_code: "ZZ-0001",
    });
    expect(edit).toHaveFocus();
  });

  it("closes with Escape and gives the focus back", async () => {
    withAccounts([SECURITIES]);
    renderApp("/accounts");
    const edit = within(await screen.findByRole("region", { name: "Conto titoli" })).getByRole(
      "button",
      { name: "Edit" },
    );
    edit.focus();
    fireEvent.click(edit);
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(edit).toHaveFocus();
  });

  it("deletes an account and its transactions only after its name is typed", async () => {
    let attempts = 0;
    const list = [SECURITIES, FOREIGN];
    const { calls } = withAccounts(list, {
      [`DELETE /api/v1/accounts/${SECURITIES.id}`]: () =>
        ++attempts === 1
          ? { status: 422, body: { code: "CONFIRMATION_MISMATCH", detail: "x" } }
          : { body: { deleted_transactions: 14 } },
    });
    const { router } = renderApp(`/accounts?account=${SECURITIES.id}`);
    fireEvent.click(
      within(await screen.findByRole("region", { name: "Conto titoli" })).getByRole("button", {
        name: "Edit",
      }),
    );
    click("Delete account…");

    const dialog = screen.getByRole("dialog", { name: "Delete the account “Conto titoli”?" });
    expect(
      within(dialog).getByText("Its 14 transactions will be deleted too. This cannot be undone."),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/archiving arrives in a later version/)).toBeInTheDocument();
    const submit = within(dialog).getByRole("button", { name: "Delete account and transactions" });
    expect(submit).toBeDisabled();
    type("Type the account name to confirm", "Conto");
    expect(submit).toBeDisabled();
    type("Type the account name to confirm", "Conto titoli ");
    expect(submit).toBeEnabled();

    fireEvent.click(submit);
    expect(
      await within(dialog).findByText("The name you typed does not match the account name."),
    ).toHaveAttribute("role", "alert");
    fireEvent.click(submit);

    const notice = await screen.findByText("Account “Conto titoli” deleted with its 14 transactions.");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("region", { name: "Conto titoli" })).toBeNull();
    // The live region takes the focus; the message is an element inside it.
    expect(notice.closest('[role="status"]')).toHaveFocus();
    expect(router.state.location.search).toBe("");
    const remove = calls.filter((c) => c.method === "DELETE").at(-1);
    expect(remove?.body).toEqual({ confirm_name: "Conto titoli " });
    expect(remove?.headers.get(CSRF_HEADER)).toBe(CSRF_VALUE);
  });

  it("says when an account has no transactions to delete", async () => {
    withAccounts([FOREIGN]);
    renderApp("/accounts");
    fireEvent.click(
      within(await screen.findByRole("region", { name: "Conto estero" })).getByRole("button", {
        name: "Edit",
      }),
    );
    expect(screen.getByLabelText("Currency")).toBeEnabled();
    click("Delete account…");
    expect(screen.getByText("It has no transactions. This cannot be undone.")).toBeInTheDocument();
  });
});
