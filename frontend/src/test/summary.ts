import type { Breakdown, Figures, PortfolioSummary } from "../api/types";

// Summary figures of an invented view with nothing in it, for the unit tests.
export const ZERO_FIGURES: Figures = {
  currency: "EUR",
  day: "2026-10-20",
  total_value: "0",
  securities_value: "0",
  cash: "0",
  invested_capital: "0",
  valued_cost: "0",
  unrealized: "0",
  unrealized_pct: null,
  realized_gross: "0",
  realized_taxes: "0",
  realized_net: "0",
  realized_pct_gross: null,
  realized_pct_net: null,
  income_gross: "0",
  income_taxes: "0",
  income_net: "0",
  taxes: "0",
  gain_gross: "0",
  gain_net: "0",
  partial: false,
};

/** The portfolio summary of a view with nothing in it. */
export const ZERO_PORTFOLIO: PortfolioSummary = {
  ...ZERO_FIGURES,
  account_count: 0,
  unavailable_currencies: [],
  stale_fx: [],
};

/** A breakdown with nothing in it. */
export const EMPTY_BREAKDOWN: Breakdown = {
  currency: "EUR",
  day: "2026-10-20",
  partial: false,
  unavailable_currencies: [],
  by_type: [],
  by_currency: [],
  by_asset: [],
  negative_cash: "0",
  income_by_month: [],
  income_by_year: [],
  income_total: { gross: "0", taxes: "0", net: "0" },
};

/** The dashboard's requests for one account or for all, answered with nothing in them. */
export function emptyDashboard(accountIds: string[]) {
  const routes: Record<string, { body: unknown }> = {
    "GET /api/v1/portfolio/summary": { body: ZERO_PORTFOLIO },
    "GET /api/v1/portfolio/positions?include_closed=true": {
      body: {
        currency: "EUR",
        day: "2026-10-20",
        partial: false,
        unavailable_currencies: [],
        stale_fx: [],
        positions: [],
      },
    },
    "GET /api/v1/portfolio/breakdown": { body: EMPTY_BREAKDOWN },
  };
  for (const id of accountIds) {
    routes[`GET /api/v1/accounts/${id}/positions?include_closed=true`] = { body: [] };
    routes[`GET /api/v1/accounts/${id}/breakdown`] = { body: EMPTY_BREAKDOWN };
  }
  return routes;
}
