import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, Asset, ChangeOut, Transaction } from "../api/types";
import i18n from "../i18n";
import { type Handler, stubFetch } from "../test/fetchStub";
import { HEALTH, ME, renderApp } from "../test/renderApp";

// Invented accounts, securities and amounts only.
const EUR: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 5,
  currency_locked: true,
};
const USD: Account = {
  ...EUR,
  id: "00000000-0000-4000-8000-0000000000a2",
  name: "Conto estero",
  currency: "USD",
};

const asset = (id: string, name: string, extra: Partial<Asset> = {}): Asset => ({
  id,
  isin: null,
  ticker: null,
  name,
  asset_type: "ETF",
  quote_currency: "EUR",
  quote_unit: "CURRENCY",
  shared: false,
  in_use: true,
  created_at: "2026-01-10T09:00:00Z",
  ...extra,
});
const WORLD = asset("00000000-0000-4000-8000-0000000000b1", "Mondo Azionario ETF");
const BANK = asset("00000000-0000-4000-8000-0000000000b2", "Banca Esempio S.p.A.", {
  asset_type: "STOCK",
});

let serial = 0;
const tx = (fields: Partial<Transaction>): Transaction => ({
  id: `00000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
  account_id: EUR.id,
  sequence: serial,
  type: "DEPOSIT",
  trade_date: "2026-09-15",
  trade_time: null,
  value_date: null,
  asset_id: null,
  quantity: null,
  unit_price: null,
  gross_amount: null,
  fees: null,
  accrued_interest_paid: null,
  accrued_interest_received: null,
  taxes: null,
  total_amount: null,
  split_ratio: null,
  affects_cash: true,
  external_ref: null,
  notes: null,
  metadata: null,
  created_at: "2026-09-15T10:00:00Z",
  updated_at: "2026-09-15T10:00:00Z",
  ...fields,
});

const ROWS: Transaction[] = [
  tx({ type: "BUY", trade_date: "2026-10-08", asset_id: WORLD.id, quantity: "15.00000000",
       unit_price: "101.80", gross_amount: "1527.00", fees: "3.00", total_amount: "1530.00" }),
  tx({ type: "DIVIDEND", trade_date: "2026-09-30", asset_id: BANK.id, quantity: "40",
       gross_amount: "52.50", taxes: "13.65", total_amount: "38.85" }),
  tx({ type: "DEPOSIT", trade_date: "2026-09-15", total_amount: "2000.00", account_id: USD.id }),
  tx({ type: "BUY", trade_date: "2026-03-02", asset_id: BANK.id, quantity: "10",
       gross_amount: "200.00", total_amount: "200.00", affects_cash: false }),
  tx({ type: "STOCK_SPLIT", trade_date: "2026-02-01", asset_id: BANK.id, split_ratio: "2:1" }),
];

// The list request with these filters (in the order the page sends them) and paging.
const list = (filters: string, offset = 0) =>
  `GET /api/v1/transactions?${filters ? `${filters}&` : ""}limit=50&offset=${offset}`;
const LIST = list("");
const page = (items: Transaction[], total: number) => ({ body: { items, total } });

function setup(routes: Record<string, Handler> = {}, accounts = [EUR, USD]) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: accounts },
    "GET /api/v1/assets": { body: [WORLD, BANK] },
    [LIST]: page(ROWS, ROWS.length),
    ...routes,
  });
}

// The first cell holds the selection box.
const cells = (row: HTMLElement) =>
  within(row)
    .getAllByRole("cell")
    .slice(1)
    .map((c) => c.textContent);

describe("transactions list", () => {
  it("shows every type with its quantity, amount and signed cash effect", async () => {
    setup();
    renderApp("/transactions");
    const heading = await screen.findByRole("heading", { name: "Transactions", level: 1 });
    expect(heading).toBeInTheDocument();
    expect(await screen.findByText("View: all accounts (2) · 5 transactions")).toBeInTheDocument();
    const nav = screen.getByRole("link", { name: "Transactions" });
    expect(nav).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("table", { name: "Transactions, rows 1–5 of 5" })).toBeInTheDocument();

    const [buy, dividend, deposit, transfer, split] = within(screen.getByRole("table"))
      .getAllByRole("row")
      .slice(1);
    expect(cells(buy)).toEqual([
      "Oct 8, 2026", "Buy", "Mondo Azionario ETF", "Conto titoli", "15", "€1,527.00", "-€1,530.00",
      "Edit",
    ]);
    // A dividend's quantity is informational: "—" (D-141).
    expect(cells(dividend)).toEqual([
      "Sep 30, 2026", "Dividend", "Banca Esempio S.p.A.", "Conto titoli", "—", "€52.50", "+€38.85",
      "Edit",
    ]);
    expect(cells(deposit)).toEqual([
      "Sep 15, 2026", "Deposit", "—", "Conto estero", "—", "$2,000.00", "+$2,000.00", "Edit",
    ]);
    expect(cells(transfer).slice(4, 7)).toEqual(["10", "€200.00", "—cash not moved"]);
    expect(cells(split).slice(1, 7)).toEqual([
      "Split", "Banca Esempio S.p.A.", "Conto titoli", "2:1", "—", "—",
    ]);
  });

  it("with one account in view: its rows only, no account column, its CSV", async () => {
    const { calls } = setup({
      [list(`account_id=${EUR.id}`)]: page([ROWS[0]], 1),
    });
    renderApp(`/transactions?account=${EUR.id}`);
    expect(await screen.findByText("View: Conto titoli · 1 transaction")).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Account" })).toBeNull();
    expect(screen.getByRole("link", { name: "Export CSV" })).toHaveAttribute(
      "href",
      `/api/v1/transactions/export.csv?account_id=${EUR.id}`,
    );
    expect(calls.some((c) => c.path === "/api/v1/transactions?limit=50&offset=0")).toBe(false);
  });

  it("keeps the filters in the URL and asks the API with them", async () => {
    setup({
      [list("type=BUY")]: page([ROWS[0], ROWS[3]], 2),
      [list(`asset_id=${BANK.id}&type=BUY`)]: page([ROWS[3]], 1),
      [list(`asset_id=${BANK.id}&type=BUY&date_from=2026-01-01`)]: page([], 0),
    });
    const { router } = renderApp("/transactions");
    await screen.findByText("View: all accounts (2) · 5 transactions");

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "BUY" } });
    expect(await screen.findByText("View: all accounts (2) · 2 transactions")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Security"), { target: { value: BANK.id } });
    expect(await screen.findByText("View: all accounts (2) · 1 transaction")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-01-01" } });
    expect(await screen.findByText("No transaction matches the filters.")).toBeInTheDocument();
    expect(router.state.location.search).toBe(`?type=BUY&asset=${BANK.id}&from=2026-01-01`);

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText("View: all accounts (2) · 5 transactions")).toBeInTheDocument();
    expect(router.state.location.search).toBe("");
  });

  it("pages 50 rows at a time; a filter goes back to the first page", async () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ ...ROWS[2], id: `id-${i}` }));
    setup({
      [LIST]: page(many, 120),
      [list("", 50)]: page(many, 120),
      [list("", 100)]: page(many.slice(0, 20), 120),
      [list("type=SELL")]: page([], 0),
    });
    const { router } = renderApp("/transactions");
    expect(await screen.findByText("Rows 1–50 of 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Rows 51–100 of 120")).toBeInTheDocument();
    expect(router.state.location.search).toBe("?page=2");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Rows 101–120 of 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "SELL" } });
    expect(await screen.findByText("No transaction matches the filters.")).toBeInTheDocument();
    expect(router.state.location.search).toBe("?type=SELL");
  });

  it("keeps the page on a reload; another account in view starts from the first page", async () => {
    const ten = Array.from({ length: 10 }, (_, i) => ({ ...ROWS[0], id: `id-${i}` }));
    setup({
      [list(`account_id=${EUR.id}`, 50)]: page(ten, 60),
      [list(`account_id=${USD.id}`)]: page([ROWS[2]], 1),
    });
    const { router } = renderApp(`/transactions?account=${EUR.id}&page=2`);
    expect(await screen.findByText("Rows 51–60 of 60")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Account in view"), { target: { value: USD.id } });
    expect(await screen.findByText("View: Conto estero · 1 transaction")).toBeInTheDocument();
    expect(router.state.location.search).toBe(`?account=${USD.id}`);
  });

  it("a page past the end offers the first page", async () => {
    setup({ [list("", 100)]: page([], 3) });
    const { router } = renderApp("/transactions?page=3");
    fireEvent.click(await screen.findByRole("button", { name: "Go to the first page" }));
    expect(await screen.findByText("Rows 1–5 of 5")).toBeInTheDocument();
    expect(router.state.location.search).toBe("");
  });

  it("says what to do when there is nothing yet", async () => {
    setup({ [LIST]: page([], 0) }, []);
    renderApp("/transactions");
    const hint = await screen.findByText("Transactions belong to an account: create one first.");
    expect(hint).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Go to Accounts" });
    expect(link).toHaveAttribute("href", "/accounts");
  });
});

const EUR2: Account = {
  ...EUR,
  id: "00000000-0000-4000-8000-0000000000a3",
  name: "Conto deposito",
};
const [BUY_WORLD, DIVIDEND_BANK, , BUY_BANK, SPLIT_BANK] = ROWS;
const SELL_BANK = tx({
  type: "SELL", trade_date: "2026-03-20", asset_id: BANK.id, quantity: "20",
  unit_price: "21.00", gross_amount: "420.00", total_amount: "420.00",
});  // prettier-ignore
const NOTHING: ChangeOut = { transactions: [], deleted: [], warnings: [] };
const BULK_EDIT = "POST /api/v1/transactions/bulk-edit";
const BULK_DELETE = "POST /api/v1/transactions/bulk-delete";
const tick = (name: string) => fireEvent.click(screen.getByRole("checkbox", { name }));
const conflictOn = (id: string) => ({
  status: 409,
  body: {
    code: "QUANTITY_CONFLICT",
    detail: "x",
    conflict: {
      reason: "NEGATIVE_QUANTITY",
      transaction_id: id,
      account_id: EUR.id,
      asset_id: BANK.id,
      quantity: "-10",
      held: "10",
    },
  },
});

describe("selection and bulk changes", () => {
  it("ticks rows of the page; the header box ticks them all; a filter clears them", async () => {
    setup({ [list("type=BUY")]: page([BUY_WORLD, BUY_BANK], 2) });
    renderApp("/transactions");
    await screen.findByText("0 selected");
    expect(screen.getByRole("button", { name: "Edit selected" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete selected" })).toBeDisabled();

    tick("Select: Buy, Mondo Azionario ETF, Oct 8, 2026");
    tick("Select: Deposit, Sep 15, 2026");
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    const all = screen.getByRole("checkbox", { name: "Select every row of this page" });
    expect(all).not.toBeChecked();
    expect(all).toHaveProperty("indeterminate", true);
    expect(screen.getByRole("button", { name: "Edit selected" })).toBeEnabled();

    fireEvent.click(all);
    expect(screen.getByText("5 selected")).toBeInTheDocument();
    expect(all).toBeChecked();
    expect(all).toHaveProperty("indeterminate", false);

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "BUY" } });
    expect(await screen.findByText("View: all accounts (2) · 2 transactions")).toBeInTheDocument();
    expect(screen.getByText("0 selected")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Mondo Azionario ETF/ })).not.toBeChecked();
  });

  it("bulk edit: accounts of the same currency, the cash option where the type has it", async () => {
    const warning = {
      account_id: EUR2.id,
      code: "NEGATIVE_CASH",
      date: "2026-09-30",
      transaction_id: DIVIDEND_BANK.id,
      introduced: true,
    };
    const { calls } = setup(
      { [BULK_EDIT]: { body: { ...NOTHING, warnings: [warning] } } },
      [EUR, USD, EUR2],
    );
    renderApp("/transactions");
    await screen.findByText("0 selected");
    tick("Select: Buy, Mondo Azionario ETF, Oct 8, 2026");
    tick("Select: Dividend, Banca Esempio S.p.A., Sep 30, 2026");
    tick("Select: Split, Banca Esempio S.p.A., Feb 1, 2026");
    fireEvent.click(screen.getByRole("button", { name: "Edit selected" }));

    const dialog = screen.getByRole("dialog", { name: "Edit 3 transactions together" });
    const account = within(dialog).getByLabelText("Account");
    const options = within(account).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["No change", "Conto titoli (EUR)", "Conto deposito (EUR)"]);
    expect(within(dialog).getByLabelText("Do not move cash")).toHaveAccessibleDescription(
      "Applies to 2 of 3: deposits, withdrawals and splits do not have this option.",
    );

    // Nothing chosen: nothing is sent.
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply to 3 transactions" }));
    expect(within(dialog).getByText(/^Nothing to change/)).toHaveAttribute("role", "alert");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    fireEvent.change(account, { target: { value: EUR2.id } });
    fireEvent.change(within(dialog).getByLabelText("Do not move cash"), {
      target: { value: "yes" },
    });
    fireEvent.change(within(dialog).getByLabelText("Notes (replace the existing ones)"), {
      target: { value: " Spostate " },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply to 3 transactions" }));
    expect(
      await screen.findByText(
        "Changes saved for 3 transactions. The cash of Conto deposito goes below zero on Sep 30, 2026.",
      ),
    ).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/transactions/bulk-edit")?.body).toEqual({
      edits: [
        { id: BUY_WORLD.id, account_id: EUR2.id, affects_cash: false, notes: "Spostate" },
        { id: DIVIDEND_BANK.id, account_id: EUR2.id, affects_cash: false, notes: "Spostate" },
        { id: SPLIT_BANK.id, account_id: EUR2.id, notes: "Spostate" },
      ],
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("0 selected")).toBeInTheDocument();
  });

  it("bulk edit across currencies cannot change the account", async () => {
    setup();
    renderApp("/transactions");
    await screen.findByText("0 selected");
    tick("Select: Buy, Banca Esempio S.p.A., Mar 2, 2026");
    tick("Select: Deposit, Sep 15, 2026");
    fireEvent.click(screen.getByRole("button", { name: "Edit selected" }));
    const dialog = screen.getByRole("dialog", { name: "Edit 2 transactions together" });
    const account = within(dialog).getByLabelText("Account");
    expect(account).toBeDisabled();
    expect(account).toHaveAccessibleDescription(
      "The selected transactions are in accounts with different currencies: " +
        "the account cannot change.",
    );
    expect(within(dialog).getByLabelText("Do not move cash")).toHaveAccessibleDescription(
      "Applies to 1 of 2: deposits, withdrawals and splits do not have this option.",
    );
  });

  it("a refused bulk edit is explained on top of it, and the choices stay", async () => {
    setup(
      {
        [LIST]: page([SELL_BANK, ...ROWS], 6),
        [BULK_EDIT]: conflictOn(SELL_BANK.id),
        [`GET /api/v1/transactions/${SELL_BANK.id}`]: { body: SELL_BANK },
      },
      [EUR, USD, EUR2],
    );
    renderApp("/transactions");
    await screen.findByText("0 selected");
    tick("Select: Buy, Banca Esempio S.p.A., Mar 2, 2026");
    fireEvent.click(screen.getByRole("button", { name: "Edit selected" }));
    const dialog = screen.getByRole("dialog", { name: "Edit 1 transaction" });
    fireEvent.change(within(dialog).getByLabelText("Account"), { target: { value: EUR2.id } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply to 1 transaction" }));

    const refused = await screen.findByRole("dialog", { name: "This change cannot be saved" });
    expect(
      await within(refused).findByText(
        "First transaction in conflict: Sell of 20 on Mar 20, 2026 (you hold 10).",
      ),
    ).toBeVisible();
    fireEvent.click(within(refused).getByRole("button", { name: "Back to the form" }));
    expect(screen.queryByRole("dialog", { name: "This change cannot be saved" })).toBeNull();
    expect(within(dialog).getByLabelText("Account")).toHaveValue(EUR2.id);
  });

  it("bulk delete: the selected rows go together; a refusal is explained", async () => {
    let refuse = true;
    const { calls } = setup({
      [BULK_DELETE]: () =>
        refuse
          ? conflictOn(SELL_BANK.id)
          : { body: { ...NOTHING, deleted: [BUY_BANK.id, SPLIT_BANK.id] } },
      [`GET /api/v1/transactions/${SELL_BANK.id}`]: { body: SELL_BANK },
    });
    renderApp("/transactions");
    await screen.findByText("0 selected");
    tick("Select: Buy, Banca Esempio S.p.A., Mar 2, 2026");
    tick("Select: Split, Banca Esempio S.p.A., Feb 1, 2026");
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = screen.getByRole("dialog", { name: "Delete 2 transactions?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete 2 transactions" }));
    const refused = await screen.findByRole("dialog", { name: "This change cannot be saved" });
    expect(await within(refused).findByText(/Sell of 20 on Mar 20, 2026/)).toBeVisible();
    fireEvent.click(within(refused).getByRole("button", { name: "Back to the form" }));

    refuse = false;
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete 2 transactions" }));
    expect(await screen.findByText("2 transactions deleted.")).toBeInTheDocument();
    const sent = calls.filter((c) => c.path === "/api/v1/transactions/bulk-delete");
    expect(sent.map((c) => c.body)).toEqual([
      { ids: [BUY_BANK.id, SPLIT_BANK.id] },
      { ids: [BUY_BANK.id, SPLIT_BANK.id] },
    ]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("no delete all on the list: it is on the holding's detail (D-146)", async () => {
    const holding = `account_id=${EUR.id}&asset_id=${BANK.id}`;
    setup({ [list(holding)]: page([DIVIDEND_BANK, BUY_BANK, SPLIT_BANK], 3) });
    renderApp(`/transactions?account=${EUR.id}&asset=${BANK.id}`);
    await screen.findByText("View: Conto titoli · 3 transactions");
    expect(screen.queryByRole("button", { name: /Delete all transactions/ })).toBeNull();
  });
});

describe("review fixes", () => {
  it("writes a split ratio in the language", async () => {
    await i18n.changeLanguage("it");
    const split = tx({ type: "STOCK_SPLIT", asset_id: BANK.id, split_ratio: "2.5:1" });
    setup({ "GET /api/v1/me": { body: { ...ME, language: "it" } }, [LIST]: page([split], 1) });
    renderApp("/transactions");
    expect(await screen.findByRole("cell", { name: "2,5:1" })).toBeInTheDocument();
  });
});
