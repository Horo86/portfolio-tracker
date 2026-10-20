import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, Asset, Transaction } from "../api/types";
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

const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);

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
    ]);
    // A dividend's quantity is informational: "—" (D-141).
    expect(cells(dividend)).toEqual([
      "Sep 30, 2026", "Dividend", "Banca Esempio S.p.A.", "Conto titoli", "—", "€52.50", "+€38.85",
    ]);
    expect(cells(deposit)).toEqual([
      "Sep 15, 2026", "Deposit", "—", "Conto estero", "—", "$2,000.00", "+$2,000.00",
    ]);
    expect(cells(transfer).slice(4)).toEqual(["10", "€200.00", "—cash not moved"]);
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
