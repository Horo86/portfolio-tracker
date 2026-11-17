import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, PortfolioPositions } from "../api/types";
import { today } from "../format/date";
import { stubFetch } from "../test/fetchStub";
import { portfolioRow, WORLD_ETF } from "../test/holdings";
import { HEALTH, ME, renderApp } from "../test/renderApp";

// The page that lists the open holdings without a price source (invented data only).

const ACCOUNT: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 4,
  currency_locked: true,
};

const NO_SOURCE = { price_status: "NOT_AVAILABLE", price: null, price_date: null, price_source: null } as const;
const BTP = portfolioRow({
  ...WORLD_ETF,
  asset_id: "00000000-0000-4000-8000-0000000000d1",
  name: "Esempio BTP 2031",
  isin: null,
  ticker: null,
  asset_type: "BOND",
  quote_unit: "PERCENT_OF_NOMINAL",
  ...NO_SOURCE,
});
const FUND = portfolioRow({
  ...WORLD_ETF,
  asset_id: "00000000-0000-4000-8000-0000000000d2",
  name: "Esempio Fondo Locale",
  quote_currency: "GBP",
  quote_unit: "MINOR_CURRENCY_UNIT",
  ...NO_SOURCE,
  price: "1520.5",
  price_date: "2026-10-05",
  price_source: "MANUAL",
});
const CLOSED = portfolioRow({ ...WORLD_ETF, asset_id: "00000000-0000-4000-8000-0000000000d3", name: "Esempio Chiuso", open: false, ...NO_SOURCE });
const WORLD = portfolioRow(WORLD_ETF);

const answer = (positions: PortfolioPositions["positions"]): PortfolioPositions => ({
  currency: "EUR",
  day: "2026-10-24",
  partial: false,
  unavailable_currencies: [],
  stale_fx: [],
  positions,
});
const HOLDINGS = "GET /api/v1/portfolio/positions?include_closed=true";
const put = (id: string) => `PUT /api/v1/assets/${id}/prices`;

function stub(positions: PortfolioPositions["positions"], extra: Parameters<typeof stubFetch>[0] = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [ACCOUNT] },
    "GET /api/v1/assets": { body: [] },
    [HOLDINGS]: { body: answer(positions) },
    ...extra,
  });
}

describe("manual prices of all securities", () => {
  it("lists the open holdings without a source, with the last manual price and the bond's own label", async () => {
    stub([BTP, FUND, CLOSED, WORLD]);
    renderApp("/holdings/prices");
    expect(await screen.findByRole("heading", { name: "Update prices by hand", level: 1 })).toBeInTheDocument();
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2); // not the closed one, not the one with a source
    expect(within(rows[0]).getByRole("link", { name: "Esempio BTP 2031" })).toHaveAttribute(
      "href",
      `/holdings/${BTP.asset_id}`,
    );
    expect(within(rows[0]).getByText("No price yet")).toBeInTheDocument();
    expect(within(rows[1]).getByText(/Oct 5, 2026/)).toBeInTheDocument();
    expect(screen.getByLabelText("Esempio BTP 2031: % of face value, clean price")).toBeInTheDocument();
    expect(screen.getByLabelText("Esempio Fondo Locale: Price (hundredths of GBP)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save prices" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "← Holdings" })).toHaveAttribute("href", "/holdings");
    expect(screen.getByLabelText("Date for all prices")).toHaveValue(today().split("-").reverse().join("/"));
  });

  it("saves the typed rows one by one: a failed row keeps its value and says why", async () => {
    const { calls } = stub([BTP, FUND], {
      [put(BTP.asset_id)]: { body: { id: "x", day: today(), close: "98.5", source: "MANUAL" } },
      [put(FUND.asset_id)]: { status: 422, body: { code: "VALIDATION_FAILED", detail: "x", issues: [{ field: "close", code: "required" }] } },
    });
    renderApp("/holdings/prices");
    const bond = await screen.findByLabelText("Esempio BTP 2031: % of face value, clean price");
    const fund = screen.getByLabelText("Esempio Fondo Locale: Price (hundredths of GBP)");
    fireEvent.change(bond, { target: { value: "98.50" } });
    fireEvent.change(fund, { target: { value: "1500" } });
    fireEvent.click(screen.getByRole("button", { name: "Save prices" }));

    await waitFor(() => expect(bond).toHaveValue(""));
    expect(within(screen.getByRole("row", { name: /Esempio BTP 2031/ })).getByRole("status")).toHaveTextContent("Saved");
    expect(fund).toHaveValue("1500");
    expect(fund).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Fill in this field.")).toBeInTheDocument();
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts.map((c) => c.body)).toEqual([
      { day: today(), close: "98.50" },
      { day: today(), close: "1500" },
    ]);
  });

  it("does not send a price that cannot be read, or a day after today", async () => {
    const { calls } = stub([BTP]);
    renderApp("/holdings/prices");
    const bond = await screen.findByLabelText("Esempio BTP 2031: % of face value, clean price");
    fireEvent.change(bond, { target: { value: "abc" } });
    expect(screen.getByText("Not a number: use digits and one decimal separator.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save prices" }));
    expect(bond).toHaveFocus();
    fireEvent.change(bond, { target: { value: "99" } });
    fireEvent.change(screen.getByLabelText("Date for all prices"), { target: { value: "01/01/2999" } });
    fireEvent.click(screen.getByRole("button", { name: "Save prices" }));
    expect(await screen.findByText("The date cannot be in the future.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  it("says that every holding has a source when none is without", async () => {
    stub([WORLD, CLOSED]);
    renderApp("/holdings/prices");
    expect(await screen.findByRole("heading", { name: "Every holding has a price source" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← Holdings" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save prices" })).toBeNull();
  });
});

describe("the button on the holdings list", () => {
  it("is there when an open holding has no source, filter on or off", async () => {
    stub([BTP, WORLD]);
    renderApp("/holdings");
    expect(await screen.findByRole("link", { name: "Update prices by hand" })).toHaveAttribute("href", "/holdings/prices");
  });

  it("is there with the filter on too", async () => {
    stub([BTP, WORLD]);
    renderApp("/holdings?source=none");
    expect(await screen.findByRole("link", { name: "Update prices by hand" })).toBeInTheDocument();
  });

  it("is not there when every open holding has a source, or the only one without is closed", async () => {
    stub([WORLD, CLOSED]);
    renderApp("/holdings");
    await screen.findByText(/open position/);
    expect(screen.queryByRole("link", { name: "Update prices by hand" })).toBeNull();
  });
});
