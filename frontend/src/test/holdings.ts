import type { AccountPosition, PortfolioHolding } from "../api/types";

// Invented holdings for the unit tests, shaped like the API's rows.

export const WORLD_ETF: AccountPosition = {
  asset_id: "00000000-0000-4000-8000-0000000000b1",
  name: "Esempio Mondo ETF",
  ticker: "ZZWLD",
  isin: "ZZ0000000017",
  asset_type: "ETF",
  quote_currency: "EUR",
  quote_unit: "CURRENCY",
  open: true,
  quantity: "50",
  pmc: "100.05000000",
  price: "108.40",
  price_date: "2026-10-20",
  price_stale: false,
  price_source: "PROVIDER",
  price_exchange: "Milan",
  price_exchange_code: null,
  price_status: "OK",
  price_status_detail: null,
  estimated_at_cost: false,
  value_unavailable: false,
  value: "5420.00000000",
  cost_basis: "5002.50",
  unrealized: "417.50000000",
  unrealized_pct: "0.08345827",
  realized_gross: "0",
  realized_taxes: "0",
  realized_net: "0",
  realized_pct_gross: null,
  realized_pct_net: null,
  income_gross: "0",
  income_taxes: "0",
  income_net: "0",
  taxes: "0",
  gain_gross: "417.50000000",
  gain_net: "417.50000000",
  fx_stale: null,
};

export function portfolioRow(
  row: AccountPosition,
  extra: Partial<PortfolioHolding> = {},
): PortfolioHolding {
  const { fx_stale, ...rest } = row;
  return {
    ...rest,
    account_ids: ["00000000-0000-4000-8000-0000000000a1"],
    valued_cost: row.cost_basis,
    partial: false,
    stale_fx: fx_stale ? [fx_stale] : [],
    ...extra,
  };
}
