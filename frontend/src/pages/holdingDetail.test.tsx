import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { TFunction } from "i18next";
import { describe, expect, it } from "vitest";

import type { Account, AccountPosition, Asset, PortfolioPositions, Transaction } from "../api/types";
import i18n from "../i18n";
import { type Handler, type Reply, stubFetch } from "../test/fetchStub";
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
const CHF: Account = { ...EUR, id: "00000000-0000-4000-8000-0000000000a3", name: "Conto svizzero", currency: "CHF" };

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
  price_exchange: null,
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
    expect(value).not.toHaveTextContent("Price from"); // a manual close names no exchange
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

  it("names the exchange an automatic price comes from, under the price (D-152)", async () => {
    const automatic = {
      ...ROBOTICS,
      price_source: "PROVIDER" as const,
      price_exchange: "NasdaqGS",
      price_status: "OK",
      price_stale: false,
    };
    detail({
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: { ...ALL, positions: [portfolioRow(automatic, { account_ids: [EUR.id, USD.id] })] },
      },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    const value = await screen.findByRole("region", { name: "Position value" });
    expect(within(value).getByText("Price from: NasdaqGS · close of Oct 10, 2026")).toBeInTheDocument();
  });

  it.each([
    ["it", "MIL", "Milan", "Prezzo da: Milano · chiusura del 10 ott 2026"],
    ["en", "EBS", "Swiss", "Price from: Zurich (SIX) · close of Oct 10, 2026"],
    ["en", "ZZX", "Borsa Esempio", "Price from: Borsa Esempio · close of Oct 10, 2026"],
  ])("names a known exchange in the user's language, any other as the engine does (%s, %s)", async (
    language,
    code,
    engineName,
    text,
  ) => {
    const automatic = {
      ...ROBOTICS,
      price_source: "PROVIDER" as const,
      price_exchange: engineName,
      price_exchange_code: code,
      price_status: "OK",
      price_stale: false,
    };
    detail({
      "GET /api/v1/me": { body: { ...ME, language } },
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: { ...ALL, positions: [portfolioRow(automatic, { account_ids: [EUR.id, USD.id] })] },
      },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it("leads to the security's manual prices from its head, keeping the account in view", async () => {
    detail({
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: { body: [ROBOTICS] },
      [`GET /api/v1/transactions?account_id=${USD.id}&asset_id=${ROBOTICS.asset_id}&limit=50&offset=0`]: {
        body: { items: [BUYS[0]], total: 1 },
      },
      [`GET /api/v1/transactions?account_id=${USD.id}&asset_id=${ROBOTICS.asset_id}&type=DIVIDEND&limit=50&offset=0`]:
        { body: { items: [], total: 0 } },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}?account=${USD.id}`);
    expect(await screen.findByRole("link", { name: "Manual prices" })).toHaveAttribute(
      "href",
      `/holdings/${ROBOTICS.asset_id}/prices?account=${USD.id}`,
    );
    // Stale since Oct 10: the price notes offer the same page.
    expect(screen.getByRole("link", { name: "Enter a price by hand" })).toHaveAttribute(
      "href",
      `/holdings/${ROBOTICS.asset_id}/prices?account=${USD.id}`,
    );
  });

  it("edits the security from its head, then says so and reloads it (D-150)", async () => {
    const renamed = { ...ASSETS[0], name: "Esempio Robotica SpA" };
    let saved = false;
    const { calls } = detail({
      "GET /api/v1/assets": () => ({ body: saved ? [renamed] : ASSETS }),
      [`PATCH /api/v1/assets/${ROBOTICS.asset_id}`]: () => {
        saved = true;
        return { body: renamed };
      },
      "GET /api/v1/currencies": { body: ["EUR", "USD"] },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    fireEvent.click(await screen.findByRole("button", { name: "Edit security" }));
    const dialog = screen.getByRole("dialog", { name: "Edit security" });
    // In use: only name and symbol go.
    expect(within(dialog).getByLabelText("Currency")).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Name"), {
      target: { value: "Esempio Robotica SpA" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Security updated.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Esempio Robotica SpA" });
    // The holdings carry the name too: they are asked again.
    const positions = "/api/v1/portfolio/positions?include_closed=true";
    await waitFor(() => expect(calls.filter((c) => c.path === positions).length).toBeGreaterThan(1));
  });

  it("asks for a price by hand only where the status needs one", async () => {
    const fresh = { ...ROBOTICS, price_source: "PROVIDER" as const, price_status: "OK", price_stale: false };
    detail({
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: { ...ALL, positions: [portfolioRow(fresh, { account_ids: [EUR.id, USD.id] })] },
      },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    expect(await screen.findByRole("link", { name: "Manual prices" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Enter a price by hand" })).toBeNull();
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

  it("keeps the confirmation when the change left the security out of the account", async () => {
    detail({ [`GET /api/v1/accounts/${EUR.id}/positions?include_closed=true`]: { body: [] } });
    const { router } = renderApp(`/holdings/${ROBOTICS.asset_id}`);
    await screen.findByRole("heading", { name: "Esempio Robotica Inc.", level: 1 });
    const text = "Transaction deleted. Cash in Conto titoli goes below zero on Feb 3, 2025.";
    await router.navigate(`/holdings/${ROBOTICS.asset_id}?account=${EUR.id}`, {
      state: { notice: text },
    });
    expect(await screen.findByText("This security is not in Conto titoli.")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(text);
  });

  it("says over all accounts which accounts are left out for a missing rate (D-73)", async () => {
    detail({
      "GET /api/v1/accounts": { body: [EUR, USD, CHF] },
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: { ...ALL, partial: true, unavailable_currencies: ["CHF"] },
      },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    expect(
      await screen.findByText("Accounts in CHF are left out: there is no exchange rate to EUR."),
    ).toBeInTheDocument();
  });

  it("never says 'no transactions' when only left-out accounts hold the security", async () => {
    detail({
      "GET /api/v1/accounts": { body: [EUR, USD, CHF] },
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: { ...ALL, partial: true, unavailable_currencies: ["CHF"], positions: [] },
      },
      [LIST]: { body: { items: [{ ...BUYS[0], account_id: CHF.id }], total: 1 } },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    expect(
      await screen.findByText(
        "This security is only in accounts left out of this view: there is no exchange rate from CHF to EUR.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See it in Conto svizzero" })).toHaveAttribute(
      "href",
      `/holdings/${ROBOTICS.asset_id}?account=${CHF.id}`,
    );
    expect(screen.queryByText("This security has no transactions.")).not.toBeInTheDocument();
  });

  it("says when the security has no transactions left", async () => {
    detail({
      "GET /api/v1/portfolio/positions?include_closed=true": { body: { ...ALL, positions: [] } },
      [LIST]: { body: { items: [], total: 0 } },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    expect(await screen.findByText("This security has no transactions.")).toBeInTheDocument();
  });

  it("shows a security with no transactions with its facts and the ways to act on it (D-150)", async () => {
    const UNUSED = { ...ASSETS[0], in_use: false };
    detail({
      "GET /api/v1/assets": { body: [UNUSED] },
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: { body: [] },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}?account=${USD.id}`);
    expect(await screen.findByRole("heading", { name: "Esempio Robotica Inc.", level: 1 })).toBeInTheDocument();
    const facts = screen.getByRole("list", { name: "Security details" });
    expect(within(facts).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Stock",
      "USD",
      "ZZ0000000025",
      "ZZROB",
    ]);
    // Not "not in this account": it is in none.
    expect(screen.getByText("This security has no transactions.")).toBeInTheDocument();
    expect(screen.queryByText(/is not in Conto estero/)).toBeNull();
    expect(screen.getByRole("link", { name: "Add transaction" })).toHaveAttribute(
      "href",
      `/transactions/new?account=${USD.id}&asset=${ROBOTICS.asset_id}`,
    );
    expect(screen.getByRole("link", { name: "Manual prices" })).toHaveAttribute(
      "href",
      `/holdings/${ROBOTICS.asset_id}/prices?account=${USD.id}`,
    );
    expect(screen.getByRole("button", { name: "Edit security" })).toBeInTheDocument();
  });

  it("shows a bond's average cost in % only in its price currency (D-143)", async () => {
    const TREASURY: AccountPosition = {
      ...ROBOTICS,
      asset_id: "00000000-0000-4000-8000-0000000000b5",
      name: "Esempio Treasury 2030",
      isin: null,
      ticker: null,
      asset_type: "BOND",
      quote_unit: "PERCENT_OF_NOMINAL",
      price_status: "MANUAL",
      quantity: "1000",
      pmc: "80.00000000",
      price: "100.00",
    };
    const ONE = `account_id=${EUR.id}&asset_id=${TREASURY.asset_id}`;
    detail({
      [`GET /api/v1/accounts/${EUR.id}/positions?include_closed=true`]: { body: [TREASURY] },
      [`GET /api/v1/transactions?${ONE}&limit=50&offset=0`]: { body: { items: [], total: 0 } },
      [`GET /api/v1/transactions?${ONE}&type=COUPON&limit=50&offset=0`]: {
        body: { items: [], total: 0 },
      },
    });
    renderApp(`/holdings/${TREASURY.asset_id}?account=${EUR.id}`); // a USD bond in a EUR account (D-66)
    const cost = await screen.findByRole("region", { name: "Cost basis" });
    expect(cost).toHaveTextContent("Average cost —");
    expect(cost).toHaveTextContent("In % of nominal only in USD");
    expect(cost).not.toHaveTextContent("80.00%");
  });

  it("goes back to the first page of transactions when the account in view changes", async () => {
    const ONE = `account_id=${USD.id}&asset_id=${ROBOTICS.asset_id}`;
    detail({
      [LIST]: { body: { items: [DIVIDEND, ...BUYS], total: 120 } },
      [`GET /api/v1/transactions?asset_id=${ROBOTICS.asset_id}&limit=50&offset=50`]: {
        body: { items: [DIVIDEND], total: 120 },
      },
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: { body: [ROBOTICS] },
      [`GET /api/v1/transactions?${ONE}&limit=50&offset=0`]: {
        body: { items: [DIVIDEND, BUYS[0]], total: 2 },
      },
      [`GET /api/v1/transactions?${ONE}&type=DIVIDEND&limit=50&offset=0`]: {
        body: { items: [DIVIDEND], total: 1 },
      },
    });
    // Both views already loaded: the page stays mounted from one to the other.
    const { router } = renderApp(`/holdings/${ROBOTICS.asset_id}?account=${USD.id}`);
    await screen.findByRole("region", { name: "Transactions of this security" });
    await router.navigate(`/holdings/${ROBOTICS.asset_id}`);
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    const txs = screen.getByRole("region", { name: "Transactions of this security" });
    expect(await within(txs).findByText("Rows 51–51 of 120")).toBeInTheDocument();
    await router.navigate(`/holdings/${ROBOTICS.asset_id}?account=${USD.id}`);
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "Transactions of this security" })).getAllByRole("row"),
      ).toHaveLength(3),
    );
  });

  it("sets a partial row's value against the cost of the parts valued (D-64)", async () => {
    detail({
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: {
          ...ALL,
          partial: true,
          positions: [
            portfolioRow(ROBOTICS, { account_ids: [EUR.id, USD.id], partial: true,
                                     valued_cost: "2400.80", cost_basis: "3200.80" }), // prettier-ignore
          ],
        },
      },
    });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    const cost = await screen.findByRole("region", { name: "Cost of the valued parts" });
    expect(cost).toHaveTextContent("€2,400.80");
    expect(cost).toHaveTextContent("Total cost basis €3,200.80");
    expect(screen.getByRole("region", { name: "Position value" })).toHaveTextContent("€2,600.00");
  });

  it("says when the income list shows only the latest, with a link to all of them", async () => {
    const older = tx({ type: "DIVIDEND", trade_date: "2025-03-01", total_amount: "4.00" });
    detail({ [INCOME]: { body: { items: [DIVIDEND, older], total: 64 } } });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    const income = await screen.findByRole("region", { name: "Dividends received" });
    expect(await within(income).findByText("The latest 2 of 64.")).toBeInTheDocument();
    const all = within(income).getByRole("link", { name: "See them all in the transactions" });
    expect(all).toHaveAttribute("href", `/transactions?asset=${ROBOTICS.asset_id}&type=DIVIDEND`);
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

  it("deletes every transaction of the security in the account in view (Q-12, D-146)", async () => {
    const ONE = `account_id=${USD.id}&asset_id=${ROBOTICS.asset_id}`;
    const { calls } = detail({
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: { body: [ROBOTICS] },
      [`GET /api/v1/transactions?${ONE}&limit=50&offset=0`]: {
        body: { items: [DIVIDEND, BUYS[0]], total: 2 },
      },
      [`GET /api/v1/transactions?${ONE}&type=DIVIDEND&limit=50&offset=0`]: {
        body: { items: [DIVIDEND], total: 1 },
      },
      [`DELETE /api/v1/accounts/${USD.id}/assets/${ROBOTICS.asset_id}/transactions`]: {
        body: { transactions: [], deleted: [DIVIDEND.id, BUYS[0].id], warnings: [] },
      },
    });
    const { router } = renderApp(`/holdings/${ROBOTICS.asset_id}?account=${USD.id}`);
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete all transactions of this security…" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Delete all transactions of a security?" });
    expect(dialog).toHaveTextContent("Esempio Robotica Inc. · Conto estero · 2 transactions.");
    fireEvent.change(within(dialog).getByLabelText("Type the name of the security to confirm"), {
      target: { value: "Esempio Robotica Inc." },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete 2 transactions" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/holdings"));
    expect(router.state.location.search).toBe(`?account=${USD.id}`);
    expect(await screen.findByText("2 transactions deleted.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });

  it("offers no delete all over all accounts", async () => {
    detail();
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    await screen.findByRole("region", { name: "Transactions of this security" });
    expect(screen.queryByRole("button", { name: /Delete all transactions/ })).toBeNull();
  });

  it("never offers the previous security's count while the next one's is on its way", async () => {
    const OTHER = { ...ROBOTICS, asset_id: "00000000-0000-4000-8000-0000000000b9", name: "Altro SpA" };
    // The next security's list answers once released, whenever its request arrives: the
    // request starts in an effect, which may run after the test goes on.
    let release = () => {};
    const answer = new Promise<Reply>((resolve) => {
      release = () => resolve({ body: { items: [BUYS[0]], total: 1 } });
    });
    const one = (id: string) => `account_id=${USD.id}&asset_id=${id}`;
    detail({
      "GET /api/v1/assets": { body: [...ASSETS, { ...ASSETS[0], id: OTHER.asset_id, name: OTHER.name }] },
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: { body: [ROBOTICS, OTHER] },
      [`GET /api/v1/transactions?${one(ROBOTICS.asset_id)}&limit=50&offset=0`]: {
        body: { items: [DIVIDEND, BUYS[0]], total: 2 },
      },
      [`GET /api/v1/transactions?${one(ROBOTICS.asset_id)}&type=DIVIDEND&limit=50&offset=0`]: {
        body: { items: [], total: 0 },
      },
      [`GET /api/v1/transactions?${one(OTHER.asset_id)}&limit=50&offset=0`]: () => answer,
      [`GET /api/v1/transactions?${one(OTHER.asset_id)}&type=DIVIDEND&limit=50&offset=0`]: {
        body: { items: [], total: 0 },
      },
    });
    const { router } = renderApp(`/holdings/${ROBOTICS.asset_id}?account=${USD.id}`);
    const deleteAll = { name: "Delete all transactions of this security…" };
    await screen.findByRole("button", deleteAll);
    await router.navigate(`/holdings/${OTHER.asset_id}?account=${USD.id}`);
    await screen.findByRole("heading", { name: "Altro SpA", level: 1 });
    expect(screen.queryByRole("button", deleteAll)).toBeNull();
    release();
    fireEvent.click(await screen.findByRole("button", deleteAll));
    expect(screen.getByRole("dialog")).toHaveTextContent("Altro SpA · Conto estero · 1 transaction.");
  });

  it("tells, on a bond's coupons only, that the total counts the accrued interest", async () => {
    const BTP: AccountPosition = {
      ...ROBOTICS,
      asset_id: "00000000-0000-4000-8000-0000000000b4",
      name: "Esempio BTP 2031",
      isin: null,
      ticker: null,
      asset_type: "BOND",
      quote_currency: "EUR",
      quote_unit: "PERCENT_OF_NOMINAL",
      price_status: "MANUAL",
      income_gross: "55.00",
      income_taxes: "6.88",
      income_net: "48.12",
    };
    const note =
      "The total takes into account the accrued interest paid on purchases and received on sales.";
    detail({
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: { ...ALL, positions: [portfolioRow(ROBOTICS), portfolioRow(BTP)] },
      },
      [`GET /api/v1/transactions?asset_id=${BTP.asset_id}&limit=50&offset=0`]: {
        body: { items: [], total: 0 },
      },
      [`GET /api/v1/transactions?asset_id=${BTP.asset_id}&type=COUPON&limit=50&offset=0`]: {
        body: { items: [], total: 0 },
      },
    });
    const { router } = renderApp(`/holdings/${BTP.asset_id}`);
    const coupons = await screen.findByRole("region", { name: "Coupons received" });
    expect(within(coupons).getByText(note)).toBeInTheDocument();
    await router.navigate(`/holdings/${ROBOTICS.asset_id}`);
    const dividends = await screen.findByRole("region", { name: "Dividends received" });
    expect(within(dividends).queryByText(note)).not.toBeInTheDocument();
  });
});

describe("deleting a security (D-150)", () => {
  const UNUSED = { ...ASSETS[0], in_use: false };
  const ONE = `/api/v1/assets/${ROBOTICS.asset_id}`;
  const PRICE = (day: string) => ({ id: `p-${day}`, day, close: "160.00", source: "MANUAL" });

  function unused(extra: Record<string, Handler> = {}) {
    return detail({
      "GET /api/v1/assets": { body: [UNUSED] },
      "GET /api/v1/portfolio/positions?include_closed=true": { body: { ...ALL, positions: [] } },
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: { body: [] },
      "GET /api/v1/currencies": { body: ["EUR", "USD"] },
      [`GET ${ONE}/prices`]: { body: [PRICE("2026-10-20"), PRICE("2026-10-21")] },
      ...extra,
    });
  }

  async function openDelete() {
    fireEvent.click(await screen.findByRole("button", { name: "Edit security" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete security" }));
    return screen.findByRole("dialog", { name: "Delete «Esempio Robotica Inc.»?" });
  }

  it("deletes it from the edit dialog, saying how many manual prices go with it", async () => {
    const { calls } = unused({ [`DELETE ${ONE}`]: { status: 204 } });
    const { router } = renderApp(`/holdings/${ROBOTICS.asset_id}?account=${USD.id}`);
    const dialog = await openDelete();
    expect(screen.queryByRole("dialog", { name: "Edit security" })).toBeNull();
    await waitFor(() =>
      expect(dialog).toHaveTextContent(
        "The security has no transactions. Your 2 manual prices will be deleted too.",
      ),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete security" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/holdings"));
    expect(router.state.location.search).toBe(`?account=${USD.id}`);
    expect(await screen.findByText("«Esempio Robotica Inc.» deleted.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });

  it("says the count of manual prices in both languages, none and one included", () => {
    const en = i18n.getFixedT("en") as TFunction;
    const it_ = i18n.getFixedT("it") as TFunction;
    expect(en("assetEdit.deleteBody", { count: 0 })).toBe("The security has no transactions.");
    expect(en("assetEdit.deleteBody", { count: 1 })).toBe(
      "The security has no transactions. Your manual price will be deleted too.",
    );
    expect(it_("assetEdit.deleteBody", { count: 0 })).toBe("Il titolo non ha transazioni.");
    expect(it_("assetEdit.deleteBody", { count: 1 })).toBe(
      "Il titolo non ha transazioni. Verrà eliminato anche il tuo prezzo a mano.",
    );
    expect(it_("assetEdit.deleteBody", { count: 3 })).toBe(
      "Il titolo non ha transazioni. Verranno eliminati anche i tuoi 3 prezzi a mano.",
    );
  });

  it("waits for the count of manual prices before it can delete", async () => {
    let release = () => {};
    const answer = new Promise<Reply>((resolve) => {
      release = () => resolve({ body: [] });
    });
    unused({ [`GET ${ONE}/prices`]: () => answer });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    const dialog = await openDelete();
    expect(within(dialog).getByRole("button", { name: "Delete security" })).toBeDisabled();
    release();
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Delete security" })).toBeEnabled());
    expect(dialog).toHaveTextContent("The security has no transactions.");
  });

  it("offers no delete for a security in use, and says why", async () => {
    detail({ "GET /api/v1/currencies": { body: ["EUR", "USD"] } });
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    fireEvent.click(await screen.findByRole("button", { name: "Edit security" }));
    const dialog = screen.getByRole("dialog", { name: "Edit security" });
    expect(dialog).toHaveTextContent("Only a security with no transactions can be deleted.");
    expect(within(dialog).queryByRole("button", { name: "Delete security" })).toBeNull();
  });

  /** As the server once a transaction was saved in another tab: in use, and held. */
  function savedMeanwhile(refusal: [string, Handler]) {
    let used = false;
    unused({
      "GET /api/v1/assets": () => ({ body: [used ? ASSETS[0] : UNUSED] }),
      "GET /api/v1/portfolio/positions?include_closed=true": () => ({
        body: used ? ALL : { ...ALL, positions: [] },
      }),
      [refusal[0]]: (call) => {
        used = true;
        return typeof refusal[1] === "function" ? refusal[1](call) : refusal[1];
      },
    });
  }

  it("says when a transaction came meanwhile (409 ASSET_IN_USE), and stays", async () => {
    savedMeanwhile([`DELETE ${ONE}`, { status: 409, body: { code: "ASSET_IN_USE" } }]);
    const { router } = renderApp(`/holdings/${ROBOTICS.asset_id}`);
    const dialog = await openDelete();
    const button = within(dialog).getByRole("button", { name: "Delete security" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Transactions use this security.");
    // The page beneath now shows the holding; the dialog stays until it is closed.
    expect(await screen.findByRole("region", { name: "Position value" })).toBeInTheDocument();
    expect(dialog).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/holdings/${ROBOTICS.asset_id}`);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit security" })).toHaveFocus());
  });

  it("keeps the edit dialog when a transaction came meanwhile, its fields now locked", async () => {
    savedMeanwhile([
      `PATCH ${ONE}`,
      { status: 409, body: { code: "ASSET_IN_USE", fields: ["quote_currency"] } },
    ]);
    renderApp(`/holdings/${ROBOTICS.asset_id}`);
    fireEvent.click(await screen.findByRole("button", { name: "Edit security" }));
    const dialog = screen.getByRole("dialog", { name: "Edit security" });
    const currency = within(dialog).getByLabelText("Currency");
    await waitFor(() => expect(currency).toHaveValue("USD"));
    fireEvent.change(currency, { target: { value: "EUR" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(within(dialog).getByRole("alert")).toHaveTextContent(
        "Transactions now use this security: currency can no longer change.",
      ),
    );
    expect(await screen.findByRole("region", { name: "Position value" })).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByLabelText("Currency")).toBeDisabled());
    expect(within(dialog).getByLabelText("Currency")).toHaveValue("USD");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit security" })).toHaveFocus());
  });

  it("goes to the holdings when it was deleted elsewhere (404)", async () => {
    unused({ [`DELETE ${ONE}`]: { status: 404, body: { code: "NOT_FOUND" } } });
    const { router } = renderApp(`/holdings/${ROBOTICS.asset_id}`);
    const dialog = await openDelete();
    const button = within(dialog).getByRole("button", { name: "Delete security" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(router.state.location.pathname).toBe("/holdings"));
    expect(await screen.findByText("This security does not exist or was deleted.")).toBeInTheDocument();
  });

  it("two quick clicks delete once", async () => {
    const { calls } = unused({ [`DELETE ${ONE}`]: { status: 204 } });
    const { router } = renderApp(`/holdings/${ROBOTICS.asset_id}`);
    const dialog = await openDelete();
    const button = within(dialog).getByRole("button", { name: "Delete security" });
    await waitFor(() => expect(button).toBeEnabled());
    // Two clicks in one task, with no render between them (outside act).
    const click = () => button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    click();
    click();
    await waitFor(() => expect(router.state.location.pathname).toBe("/holdings"));
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });
});
