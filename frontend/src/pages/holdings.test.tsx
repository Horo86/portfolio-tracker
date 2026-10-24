import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, AccountPosition, PortfolioPositions } from "../api/types";
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
  gain_gross: "199.20000000",
  gain_net: "199.20000000",
};
const BTP: AccountPosition = {
  ...WORLD_ETF,
  asset_id: "00000000-0000-4000-8000-0000000000b3",
  name: "Esempio BTP 2031",
  ticker: null,
  isin: null,
  asset_type: "BOND",
  quote_unit: "PERCENT_OF_NOMINAL",
  open: false,
  quantity: "0",
  pmc: null,
  price: null,
  price_date: null,
  price_source: null,
  price_status: "MANUAL",
  value: "0",
  cost_basis: "0",
  unrealized: "0",
  unrealized_pct: null,
  realized_gross: "-12.50",
  gain_gross: "-12.50",
  gain_net: "-12.50",
};

const STALE = { from_currency: "USD", to_currency: "EUR", rate_date: "2026-10-12" };

const ALL: PortfolioPositions = {
  currency: "EUR",
  day: "2026-10-24",
  partial: false,
  unavailable_currencies: [],
  stale_fx: [],
  positions: [
    portfolioRow(BTP),
    portfolioRow(WORLD_ETF),
    portfolioRow(ROBOTICS, { account_ids: [EUR.id, USD.id] }),
  ],
};

function holdings(extra: Record<string, Handler> = {}, me: object = ME) {
  return stubFetch({
    "GET /api/v1/me": { body: me },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [EUR, USD] },
    "GET /api/v1/portfolio/positions?include_closed=true": { body: ALL },
    ...extra,
  });
}

const rowNames = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getByRole("rowheader").querySelector("a")?.textContent);

describe("holdings list (mockup 02, D-143, D-146)", () => {
  it("shows all accounts in one row per security, in the reporting currency", async () => {
    holdings();
    renderApp("/holdings");
    expect(await screen.findByRole("heading", { name: "Holdings", level: 1 })).toBeInTheDocument();
    expect(
      await screen.findByText("View: all accounts (2) · values in EUR · 2 open positions"),
    ).toBeInTheDocument();
    expect(rowNames()).toEqual(["Esempio Mondo ETF", "Esempio Robotica Inc."]); // closed: hidden
    expect(screen.getByRole("columnheader", { name: "Average cost (EUR)" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: /^Esempio Mondo ETF/ })).toBeInTheDocument();
    const robotics = screen.getByRole("row", { name: /Esempio Robotica/ });
    expect(within(robotics).getByText("Conto titoli")).toBeInTheDocument();
    expect(within(robotics).getByText("Conto estero")).toBeInTheDocument();
    expect(within(robotics).getByText("€120.04")).toBeInTheDocument(); // the PMC in EUR
    expect(within(robotics).getByText("$162.50")).toBeInTheDocument(); // the price in USD
    expect(within(robotics).getByText("+8.30% unrealized")).toBeInTheDocument();
    expect(within(robotics).getByText("Looking up prices")).toBeInTheDocument();
    expect(
      within(robotics).getByText("Price not updated since Oct 10, 2026 · next daily update"),
    ).toBeInTheDocument();
    expect(within(robotics).getByText("Manual price")).toBeInTheDocument();
    expect(screen.getByText(/at the latest rate, the average cost too/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Esempio Mondo ETF" })).toHaveAttribute(
      "href",
      `/holdings/${WORLD_ETF.asset_id}`,
    );
  });

  it("shows one account's part, in its currency, without an account column", async () => {
    holdings({
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: {
        body: [{ ...ROBOTICS, pmc: "150.05000000", value: "3250.00000000" }],
      },
    });
    renderApp(`/holdings?account=${USD.id}`);
    expect(
      await screen.findByText("View: Conto estero · values in USD · 1 open position"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Account" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Average cost (USD)" })).toBeInTheDocument();
    expect(screen.getByText("$150.05")).toBeInTheDocument();
    expect(screen.queryByText(/A security held in more than one account/)).not.toBeInTheDocument();
  });

  it("shows a bond's average cost in % only in its price currency (D-143)", async () => {
    const TREASURY: AccountPosition = {
      ...BTP,
      asset_id: "00000000-0000-4000-8000-0000000000b5",
      name: "Esempio Treasury 2030",
      quote_currency: "USD",
      open: true,
      quantity: "1000",
      pmc: "80.00000000", // 1,000 USD at 1.25 USD per EUR: 800 EUR for 1,000 of nominal
      price: "100.00",
      price_date: "2026-10-20",
      value: "800.00000000",
      cost_basis: "800.00000000",
    };
    holdings({
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: { ...ALL, positions: [portfolioRow(TREASURY, { account_ids: [USD.id] })] },
      },
      [`GET /api/v1/accounts/${USD.id}/positions?include_closed=true`]: {
        body: [{ ...TREASURY, pmc: "100.00000000", value: "1000.00000000" }],
      },
    });
    const { router } = renderApp("/holdings");
    const row = await screen.findByRole("row", { name: /Esempio Treasury/ });
    const pmc = within(row).getAllByRole("cell")[2];
    expect(pmc).toHaveTextContent("—In % of nominal only in USD");
    expect(within(row).queryByText("80.00%")).not.toBeInTheDocument();
    await router.navigate(`/holdings?account=${USD.id}`);
    await screen.findByText("View: Conto estero · values in USD · 1 open position");
    const inUsd = screen.getByRole("row", { name: /Esempio Treasury/ });
    expect(within(inUsd).getAllByRole("cell")[1]).toHaveTextContent(/^100\.00%$/);
  });

  it("filters by type, currency and closed positions through the URL, and by text", async () => {
    holdings();
    const { router } = renderApp("/holdings");
    await screen.findByText("Esempio Robotica Inc.");
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "STOCK" } });
    expect(rowNames()).toEqual(["Esempio Robotica Inc."]);
    expect(router.state.location.search).toBe("?type=STOCK");
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    fireEvent.click(screen.getByLabelText("Show closed positions"));
    expect(router.state.location.search).toBe("?closed=1");
    expect(rowNames()).toEqual(["Esempio BTP 2031", "Esempio Mondo ETF", "Esempio Robotica Inc."]);
    expect(within(screen.getByRole("row", { name: /BTP/ })).getByText(/closed position/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "USD" } });
    expect(rowNames()).toEqual(["Esempio Robotica Inc."]);
    fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "zz0000000017" } });
    expect(rowNames()).toEqual(["Esempio Mondo ETF"]); // by ISIN, any case
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "nothing" } });
    expect(screen.getByText("No security matches the chosen filters.")).toBeInTheDocument();
  });

  it("says when market data is off instead of a lookup (D-145)", async () => {
    holdings({}, { ...ME, market_data: "OFF" });
    renderApp("/holdings");
    const robotics = await screen.findByRole("row", { name: /Esempio Robotica/ });
    expect(within(robotics).getByText("Automatic prices off")).toBeInTheDocument();
    expect(within(robotics).getByText("Price not updated since Oct 10, 2026")).toBeInTheDocument();
    expect(screen.queryByText("Looking up prices")).not.toBeInTheDocument();
  });

  it("labels partial totals and names the accounts left out (D-64, D-73)", async () => {
    holdings({
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: {
          ...ALL,
          partial: true,
          unavailable_currencies: ["USD"],
          stale_fx: [STALE],
          positions: [portfolioRow(ROBOTICS, { partial: true, stale_fx: [STALE] })],
        },
      },
    });
    renderApp("/holdings");
    expect(await screen.findByText(/Partial: an exchange rate is missing/)).toBeInTheDocument();
    expect(
      screen.getByText("Accounts in USD are left out: there is no exchange rate to EUR."),
    ).toBeInTheDocument();
    expect(screen.getByText(/USD\/EUR/)).toBeInTheDocument();
    expect(
      within(screen.getByRole("row", { name: /Esempio Robotica/ })).getByText("Partial"),
    ).toBeInTheDocument();
  });

  it("names a stale rate only while a row that uses it is shown", async () => {
    const closed = portfolioRow(BTP, { stale_fx: [STALE] });
    holdings({
      "GET /api/v1/portfolio/positions?include_closed=true": {
        body: { ...ALL, stale_fx: [STALE], positions: [closed, portfolioRow(WORLD_ETF)] },
      },
    });
    renderApp("/holdings");
    await screen.findByRole("row", { name: /Esempio Mondo ETF/ });
    expect(screen.queryByText(/USD\/EUR/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Show closed positions"));
    expect(await screen.findByText(/USD\/EUR/)).toBeInTheDocument();
  });

  it("explains an empty view", async () => {
    holdings({
      "GET /api/v1/portfolio/positions?include_closed=true": { body: { ...ALL, positions: [] } },
    });
    renderApp("/holdings");
    expect(await screen.findByText("No securities in this view")).toBeInTheDocument();
    expect(screen.queryByLabelText("Search")).not.toBeInTheDocument();
  });

  it("is in the main menu, after Accounts", async () => {
    holdings();
    renderApp("/holdings");
    const menu = await screen.findByRole("navigation", { name: "Main menu" });
    const names = within(menu)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(names.indexOf("Holdings")).toBe(names.indexOf("Accounts") + 1);
  });
});
