import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  Account,
  AccountPosition,
  AccountSummary,
  Breakdown,
  PortfolioPositions,
  PortfolioSummary,
} from "../api/types";
import { type Handler, stubFetch } from "../test/fetchStub";
import { portfolioRow, WORLD_ETF } from "../test/holdings";
import { HEALTH, ME, renderApp } from "../test/renderApp";
import { ZERO_FIGURES } from "../test/summary";

// Invented accounts, securities and figures only; the figures need not add up, the page
// shows what the server computed.
const EUR: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 6,
  currency_locked: true,
};
const USD: Account = {
  ...EUR,
  id: "00000000-0000-4000-8000-0000000000a2",
  name: "Conto estero",
  currency: "USD",
  transaction_count: 2,
};

const ROBOTICS: AccountPosition = {
  ...WORLD_ETF,
  asset_id: "00000000-0000-4000-8000-0000000000b2",
  name: "Esempio Robotica Inc.",
  isin: "ZZ0000000025",
  asset_type: "STOCK",
  quote_currency: "USD",
  unrealized_pct: "0.382",
};
const ENERGY: AccountPosition = {
  ...WORLD_ETF,
  asset_id: "00000000-0000-4000-8000-0000000000b3",
  name: "Esempio Energia ETF",
  unrealized_pct: "-0.094",
};

const SUMMARY: PortfolioSummary = {
  ...ZERO_FIGURES,
  day: "2026-10-24",
  total_value: "31482.60",
  securities_value: "30214.40",
  cash: "1268.20",
  invested_capital: "25640.00",
  valued_cost: "25640.00",
  unrealized: "4574.40",
  unrealized_pct: "0.17840874",
  realized_gross: "620.30",
  realized_taxes: "161.28",
  realized_net: "459.02",
  realized_pct_gross: "0.1513",
  realized_pct_net: "0.112",
  income_gross: "842.75",
  income_taxes: "174.23",
  income_net: "668.52",
  taxes: "335.51",
  gain_gross: "6037.45",
  gain_net: "5701.94",
  account_count: 2,
  unavailable_currencies: [],
  stale_fx: [],
};

const POSITIONS: PortfolioPositions = {
  currency: "EUR",
  day: "2026-10-24",
  partial: false,
  unavailable_currencies: [],
  stale_fx: [],
  positions: [portfolioRow(ENERGY), portfolioRow(WORLD_ETF), portfolioRow(ROBOTICS)],
};

const months = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
  .concat(["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"])
  .map((month) => ({ month, gross: "0", taxes: "0", net: "0" }));
months[1] = { month: "2025-12", gross: "180.00", taxes: "46.80", net: "133.20" };
months[10] = { month: "2026-09", gross: "72.00", taxes: "18.72", net: "53.28" };

const BREAKDOWN: Breakdown = {
  currency: "EUR",
  day: "2026-10-24",
  partial: false,
  unavailable_currencies: [],
  by_type: [
    { key: "ETF", value: "18889.56", share: "0.6" },
    { key: "STOCK", value: "6328.00", share: "0.201" },
    { key: "BOND", value: "5005.74", share: "0.159" },
    { key: "CASH", value: "1259.30", share: "0.04" },
  ],
  by_currency: [
    { key: "EUR", value: "23360.10", share: "0.742" },
    { key: "USD", value: "6548.39", share: "0.208" },
    { key: "GBP", value: "1574.11", share: "0.05" },
  ],
  by_asset: [
    { asset_id: WORLD_ETF.asset_id, name: "Esempio Mondo ETF", value: "12000.00", share: "0.39628" },
    { asset_id: ROBOTICS.asset_id, name: "Esempio Robotica Inc.", value: "6328.00", share: "0.20897" },
  ],
  negative_cash: "0",
  income_by_month: months,
  income_by_year: [
    { year: 2025, gross: "590.75", taxes: "128.00", net: "462.75" },
    { year: 2026, gross: "252.00", taxes: "46.23", net: "205.77" },
  ],
  income_total: { gross: "842.75", taxes: "174.23", net: "668.52" },
};

const ACCOUNT_SUMMARY: AccountSummary = {
  ...SUMMARY,
  currency: "USD",
  total_value: "7100.00",
  account_id: USD.id,
  open_positions: 1,
  stale_fx: [],
  reporting: { currency: "EUR", total_value: "6548.39", stale_fx: null },
};

function dashboard(extra: Record<string, Handler> = {}, accounts = [EUR, USD]) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: accounts },
    "GET /api/v1/portfolio/summary": { body: SUMMARY },
    "GET /api/v1/portfolio/positions?include_closed=true": { body: POSITIONS },
    "GET /api/v1/portfolio/breakdown": { body: BREAKDOWN },
    ...extra,
  });
}

const region = (name: string) => screen.getByRole("region", { name });
const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);

describe("dashboard (mockup 01, D-153)", () => {
  it("greets the user and names the view, its currency and day", async () => {
    dashboard();
    renderApp("/");
    expect(
      await screen.findByRole("heading", { name: "Hello Mario, here are your investments", level: 1 }),
    ).toBeInTheDocument();
    expect(document.title).toBe("Dashboard · Portfolio Tracker");
    expect(
      await screen.findByText("View: all accounts (2) · values in EUR as of Oct 24, 2026"),
    ).toBeInTheDocument();
    // An account in another currency: converted at the latest rate (§9, UI help).
    expect(screen.getByText(/converted at the latest rate, past gains and income too/)).toBeInTheDocument();
  });

  it("shows the three cards and where the gain comes from", async () => {
    dashboard();
    renderApp("/");
    const total = await screen.findByRole("region", { name: "Total value" });
    expect(total).toHaveTextContent("€31,482.60");
    expect(total).toHaveTextContent("Securities €30,214.40 · Cash €1,268.20");
    expect(region("Invested capital")).toHaveTextContent("€25,640.00");
    expect(region("Invested capital")).toHaveTextContent("Cost basis of the open positions");
    expect(region("Net gain")).toHaveTextContent("+€5,701.94");
    expect(region("Net gain")).toHaveTextContent("Gross +€6,037.45 · taxes €335.51");

    const gains = region("Where the gain comes from");
    const row = (name: RegExp) => within(gains).getByRole("row", { name });
    expect(cells(row(/^Unrealized/))).toEqual(["+€4,574.40", "+€4,574.40"]);
    expect(row(/^Unrealized/)).toHaveTextContent("+17.84% on the cost basis");
    expect(cells(row(/^Realized/))).toEqual(["+€620.30", "+€459.02"]);
    expect(row(/^Realized/)).toHaveTextContent("gross +15.13% · net +11.20% on the cost sold");
    expect(cells(row(/^Dividends and coupons/))).toEqual(["+€842.75", "+€668.52"]);
    expect(row(/^Dividends and coupons/)).toHaveTextContent("in money only");
    expect(cells(row(/^Total gain/))).toEqual(["+€6,037.45", "+€5,701.94"]);
    expect(gains).toHaveTextContent(
      "Taxes withheld: €335.51 (€161.28 on sales, €174.23 on income).",
    );
  });

  it("shows the allocation by type, by currency and by security as tables", async () => {
    dashboard();
    renderApp("/");
    const byType = await screen.findByRole("table", { name: "By type" });
    expect(within(byType).getAllByRole("row").slice(1).map((r) => r.textContent)).toEqual([
      "ETF€18,889.5660.00%",
      "Stock€6,328.0020.10%",
      "Bond€5,005.7415.90%",
      "Cash€1,259.304.00%",
    ]);
    const byCurrency = screen.getByRole("table", { name: "By currency" });
    expect(within(byCurrency).getByRole("row", { name: /^USD/ })).toHaveTextContent("€6,548.3920.80%");
    const byAsset = screen.getByRole("table", { name: "By security" });
    expect(within(byAsset).getByRole("link", { name: "Esempio Mondo ETF" })).toHaveAttribute(
      "href",
      `/holdings/${WORLD_ETF.asset_id}`,
    );
    expect(within(byAsset).getByRole("row", { name: /^Esempio Robotica/ })).toHaveTextContent(
      "€6,328.0020.90%",
    );
    expect(screen.queryByText(/Negative cash/)).not.toBeInTheDocument();
  });

  it("lists the 10 largest securities and the others on request", async () => {
    const many = Array.from({ length: 12 }, (_, i) => ({
      asset_id: `00000000-0000-4000-8000-0000000001${String(i).padStart(2, "0")}`,
      name: `Esempio Titolo ${String(i + 1).padStart(2, "0")}`,
      value: `${1200 - i * 10}.00`,
      share: "0.08",
    }));
    dashboard({ "GET /api/v1/portfolio/breakdown": { body: { ...BREAKDOWN, by_asset: many } } });
    renderApp("/");
    const byAsset = await screen.findByRole("table", { name: "By security" });
    expect(within(byAsset).getAllByRole("link")).toHaveLength(10);
    expect(within(byAsset).queryByText("Esempio Titolo 11")).not.toBeInTheDocument();
    const others = within(byAsset).getByRole("button", { name: "Others (2)" });
    expect(others).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(others);
    expect(within(byAsset).getAllByRole("link")).toHaveLength(12);
    expect(within(byAsset).queryByRole("button")).not.toBeInTheDocument();
    // The button goes away: the focus moves to the first security it showed.
    expect(within(byAsset).getByRole("rowheader", { name: "Esempio Titolo 11" })).toHaveFocus();
  });

  it("ranks the best and worst securities by unrealized %", async () => {
    dashboard();
    renderApp("/");
    const best = await screen.findByRole("region", { name: "Best and worst securities" });
    expect(best).toHaveTextContent("Unrealized gain in % of the cost basis.");
    const items = within(best).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "Esempio Robotica Inc.Stock · USD+38.20%",
      "Esempio Mondo ETFETF · EUR+8.35%",
      "Esempio Energia ETFETF · EUR-9.40%",
    ]);
    expect(within(items[0]).getByRole("link")).toHaveAttribute(
      "href",
      `/holdings/${ROBOTICS.asset_id}`,
    );
  });

  it("lists the dividends and coupons by month and by year", async () => {
    dashboard();
    renderApp("/");
    const income = await screen.findByRole("region", { name: "Dividends and coupons received" });
    expect(income).toHaveTextContent("Last 12 months, before withholding");
    const monthly = within(income).getByRole("table", { name: "Last 12 months, before withholding" });
    const rows = within(monthly).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(12);
    expect(rows[0]).toHaveTextContent("Nov 2025€0.00€0.00");
    expect(rows[1]).toHaveTextContent("Dec 2025€180.00€133.20");
    expect(rows[11]).toHaveTextContent("Oct 2026€0.00€0.00");
    expect(income).toHaveTextContent("Since the start: €842.75 gross · €668.52 net");
    const yearly = within(income).getByRole("table", { name: "By year" });
    expect(within(yearly).getAllByRole("row").slice(1).map((r) => r.textContent)).toEqual([
      "2025€590.75€462.75",
      "2026€252.00€205.77",
    ]);
    expect(income).toHaveTextContent(
      "Only dividends and coupons received: total income also counts accrued interest and the taxes on bond purchases.",
    );
  });

  it("shows one account in its own currency, with its own routes", async () => {
    const positions = [{ ...ROBOTICS, fx_stale: null }];
    dashboard({
      [`GET /api/v1/accounts/${USD.id}/summary`]: { body: ACCOUNT_SUMMARY },
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: { body: positions },
      [`GET /api/v1/accounts/${USD.id}/breakdown`]: {
        body: { ...BREAKDOWN, currency: "USD", by_type: [{ key: "STOCK", value: "7100.00", share: "1" }] },
      },
    });
    renderApp(`/?account=${USD.id}`);
    expect(
      await screen.findByText("View: Conto estero · values in USD as of Oct 24, 2026"),
    ).toBeInTheDocument();
    expect(region("Total value")).toHaveTextContent("$7,100.00");
    const byType = await screen.findByRole("table", { name: "By type" });
    expect(within(byType).getByRole("row", { name: /^Stock/ })).toHaveTextContent("$7,100.00100.00%");
    expect(screen.queryByText(/converted at the latest rate/)).not.toBeInTheDocument();
  });
});

describe("dashboard banner (D-153 (6), Q-15)", () => {
  it("lists the securities whose price needs the user, each linking to its detail", async () => {
    const failing = portfolioRow({ ...ENERGY, price_status: "FAILING" });
    const stale = portfolioRow({ ...ROBOTICS, price_stale: true, price_date: "2026-10-10" });
    const pending = portfolioRow({ ...WORLD_ETF, price_status: "PENDING" });
    dashboard({
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: { ...POSITIONS, positions: [failing, pending, stale] },
      },
    });
    renderApp("/");
    const line = await screen.findByRole("link", { name: "Esempio Energia ETF: Update failed" });
    expect(line).toHaveAttribute("href", `/holdings/${ENERGY.asset_id}`);
    expect(
      screen.getByRole("link", {
        name: "Esempio Robotica Inc.: Price not updated since Oct 10, 2026 · next daily update",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Automatic prices are being looked up for 1 security.")).toBeInTheDocument();
  });

  it("says each stale rate once per pair of currencies", async () => {
    const stale_fx = [
      { from_currency: "USD", to_currency: "EUR", rate_date: "2026-10-12" },
      { from_currency: "EUR", to_currency: "USD", rate_date: "2026-10-12" },
    ];
    dashboard({ "GET /api/v1/portfolio/summary": { body: { ...SUMMARY, stale_fx } } });
    renderApp("/");
    expect(
      await screen.findAllByText(
        "The USD/EUR exchange rate is from Oct 12, 2026: it has not been updated recently.",
      ),
    ).toHaveLength(1);
    expect(screen.queryByText(/The EUR\/USD exchange rate/)).not.toBeInTheDocument();
  });

  it("past 6 securities shows 5 and a link to the holdings", async () => {
    const rows = Array.from({ length: 8 }, (_, i) =>
      portfolioRow({
        ...ENERGY,
        asset_id: `00000000-0000-4000-8000-0000000002${String(i).padStart(2, "0")}`,
        name: `Esempio Fondo ${i + 1}`,
        price_status: "NOT_AVAILABLE",
      }),
    );
    dashboard({
      "GET /api/v1/portfolio/positions?include_closed=true": { body: { ...POSITIONS, positions: rows } },
    });
    renderApp("/");
    expect(await screen.findByRole("link", { name: "Esempio Fondo 5: No price source" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Esempio Fondo 6: No price source" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "and 3 more securities: see Holdings" })).toHaveAttribute(
      "href",
      "/holdings",
    );
  });
});
