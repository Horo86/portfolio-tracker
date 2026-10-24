import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, AccountPosition, Asset, PortfolioPositions, Transaction } from "../api/types";
import { type Handler, stubFetch } from "../test/fetchStub";
import { portfolioRow, WORLD_ETF } from "../test/holdings";
import { HEALTH, ME, renderApp } from "../test/renderApp";

const EUR: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 4,
  currency_locked: true,
};
const USD: Account = { ...EUR, id: "00000000-0000-4000-8000-0000000000a2", name: "Conto estero", currency: "USD" };

const ROBOTICS: AccountPosition = {
  ...WORLD_ETF,
  asset_id: "00000000-0000-4000-8000-0000000000b2",
  name: "Esempio Robotica Inc.",
  ticker: "ZZROB",
  isin: "ZZ0000000025",
  asset_type: "STOCK",
  quote_currency: "USD",
  quantity: "20",
  pmc: "120.04000000",
  price: "162.50",
  price_date: "2026-10-10",
  price_stale: true,
  price_source: "MANUAL",
  price_status: "PENDING",
  value: "2600.00000000",
  cost_basis: "2400.80000000",
  unrealized: "199.20000000",
  unrealized_pct: "0.08297234",
  realized_gross: "40.00",
  realized_taxes: "10.40",
  realized_net: "29.60",
  realized_pct_gross: "0.2",
  realized_pct_net: "0.148",
  income_gross: "5.00",
  income_taxes: "1.00",
  income_net: "4.00",
  taxes: "11.40",
  gain_gross: "244.20000000",
  gain_net: "232.80000000",
};

const ASSETS: Asset[] = [
  {
    id: ROBOTICS.asset_id,
    isin: ROBOTICS.isin,
    ticker: ROBOTICS.ticker,
    name: ROBOTICS.name,
    asset_type: "STOCK",
    quote_currency: "USD",
    quote_unit: "CURRENCY",
    shared: false,
    in_use: true,
    created_at: "2026-01-10T09:00:00Z",
  },
];

let serial = 0;
const tx = (fields: Partial<Transaction>): Transaction => ({
  id: `00000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
  account_id: USD.id,
  sequence: serial,
  type: "BUY",
  trade_date: "2025-05-05",
  trade_time: null,
  value_date: null,
  asset_id: ROBOTICS.asset_id,
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

const DIVIDEND = tx({ type: "DIVIDEND", trade_date: "2025-09-01", gross_amount: "5.00",
                      taxes: "1.00", total_amount: "4.00" }); // prettier-ignore
const BUYS = [
  tx({ trade_date: "2025-05-05", quantity: "20", unit_price: "150.05", gross_amount: "3001.00",
       total_amount: "3001.00" }), // prettier-ignore
  tx({ account_id: EUR.id, trade_date: "2025-02-03", quantity: "2", unit_price: "100.00",
       gross_amount: "180.00", total_amount: "180.00", affects_cash: false }), // prettier-ignore
];

const ALL: PortfolioPositions = {
  currency: "EUR",
  day: "2026-10-24",
  partial: false,
  unavailable_currencies: [],
  stale_fx: [],
  positions: [portfolioRow(ROBOTICS, { account_ids: [EUR.id, USD.id] })],
};

const LIST = `GET /api/v1/transactions?asset_id=${ROBOTICS.asset_id}&limit=50&offset=0`;
const INCOME = `GET /api/v1/transactions?asset_id=${ROBOTICS.asset_id}&type=DIVIDEND&limit=50&offset=0`;

function detail(extra: Record<string, Handler> = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [EUR, USD] },
    "GET /api/v1/assets": { body: ASSETS },
    "GET /api/v1/portfolio/positions?include_closed=true": { body: ALL },
    [LIST]: { body: { items: [DIVIDEND, ...BUYS], total: 3 } },
    [INCOME]: { body: { items: [DIVIDEND], total: 1 } },
    ...extra,
  });
}

describe("holding detail (mockup 03 without the chart, D-146)", () => {
  it("shows the holding over all accounts: status, figures, gains, income, transactions", async () => {
    detail();
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    expect(await screen.findByRole("heading", { name: "Esempio Robotica Inc.", level: 1 })).toBeInTheDocument();
    const facts = screen.getByRole("list", { name: "Security details" });
    expect(within(facts).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Stock",
      "USD",
      "ZZ0000000025",
      "ZZROB",
      "Conto titoli",
      "Conto estero",
    ]);
    expect(screen.getByText("Looking up automatic prices: this can take a few daily updates.")).toBeInTheDocument();
    expect(screen.getByText("Price not updated since Oct 10, 2026 · next daily update")).toBeInTheDocument();
    expect(screen.getByText("Manual price")).toBeInTheDocument();

    const value = screen.getByRole("region", { name: "Position value" });
    expect(value).toHaveTextContent("€2,600.00");
    expect(value).toHaveTextContent("20 units × $162.50 · price of Oct 10, 2026");
    expect(screen.getByRole("region", { name: "Cost basis" })).toHaveTextContent("Average cost €120.04");
    const gain = screen.getByRole("region", { name: "Net gain on this security" });
    expect(gain).toHaveTextContent("+€232.80");
    expect(gain).toHaveTextContent("Gross +€244.20 · taxes €11.40");

    const gains = screen.getByRole("region", { name: "Where the gain comes from" });
    expect(within(gains).getByRole("row", { name: /Unrealized/ })).toHaveTextContent("+8.30% on the cost basis");
    expect(within(gains).getByRole("row", { name: /Realized/ })).toHaveTextContent(
      "gross +20.00% · net +14.80% on the cost sold",
    );
    expect(within(gains).getByRole("row", { name: /Total gain/ })).toHaveTextContent("+€244.20+€232.80");

    const income = screen.getByRole("region", { name: "Dividends received" });
    expect(await within(income).findByText("$4.00 net")).toBeInTheDocument(); // the account's currency

    const txs = screen.getByRole("region", { name: "Transactions of this security" });
    expect(within(txs).getByRole("columnheader", { name: "Account" })).toBeInTheDocument();
    expect(within(txs).getAllByRole("row")).toHaveLength(4);
    expect(within(txs).getByText("cash not moved")).toBeInTheDocument();
    expect(within(txs).getByRole("link", { name: "Edit several transactions" })).toHaveAttribute(
      "href",
      `/transactions?asset=${ROBOTICS.asset_id}`,
    );
    expect(screen.getByRole("link", { name: "Add transaction" })).toHaveAttribute(
      "href",
      `/transactions/new?asset=${ROBOTICS.asset_id}`,
    );
  });

  it("shows one account's part and asks for its transactions only", async () => {
    detail({
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: { body: [ROBOTICS] },
      [`GET /api/v1/transactions?account_id=${USD.id}&asset_id=${ROBOTICS.asset_id}&limit=50&offset=0`]: {
        body: { items: [DIVIDEND, BUYS[0]], total: 2 },
      },
      [`GET /api/v1/transactions?account_id=${USD.id}&asset_id=${ROBOTICS.asset_id}&type=DIVIDEND&limit=50&offset=0`]:
        { body: { items: [DIVIDEND], total: 1 } },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}?account=${USD.id}`);
    const txs = await screen.findByRole("region", { name: "Transactions of this security" });
    expect(await within(txs).findAllByRole("row")).toHaveLength(3);
    expect(within(txs).queryByRole("columnheader", { name: "Account" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← Holdings" })).toHaveAttribute(
      "href",
      `/holdings?account=${USD.id}`,
    );
  });

  it("says when the account in view does not hold it, with a link to all accounts", async () => {
    detail({ [`GET /api/v1/accounts/${EUR.id}/positions?include_closed=true`]: { body: [] } });
    renderApp(`/holdings/${ROBOTICS.asset_id}?account=${EUR.id}`);
    expect(await screen.findByText("This security is not in Conto titoli.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See it in all accounts" })).toHaveAttribute(
      "href",
      `/holdings/${ROBOTICS.asset_id}`,
    );
  });

  it("says when the security does not exist", async () => {
    detail();
    renderApp("/holdings/00000000-0000-4000-8000-0000000000ff");
    expect(await screen.findByText("This security does not exist or was deleted.")).toBeInTheDocument();
  });

  it("opens the form with the security chosen, telling it to come back to the detail", async () => {
    detail();
    const { router } = renderApp(`/holdings/${ROBOTICS.asset_id}`);
    await screen.findByRole("heading", { name: "Esempio Robotica Inc.", level: 1 });
    expect(router.state.location.pathname).toBe(`/holdings/${ROBOTICS.asset_id}`);
    fireEvent.click(screen.getByRole("link", { name: "Add transaction" }));
    expect(await screen.findByLabelText("Security")).toHaveValue(ROBOTICS.asset_id);
    expect(router.state.location.state).toEqual({
      backPath: `/holdings/${ROBOTICS.asset_id}`,
      back: "",
    });
  });
});
