// The search of screen 11 (D-128, D-148): the provider's results as the page shows them.
// Pure: no React, no request.

import type { AssetType, QuoteUnit, SearchResult } from "./api/types";

/** One row of the results: what "Aggiungi" puts into the create form. */
export type SearchRow = {
  key: string;
  name: string; // "" when no listing named it
  isin: string | null; // null: a text search, manual prices only
  ticker: string; // the provider's symbol, copied as the ticker (D-175 (3))
  symbol: string; // the provider's symbol of this result, shown on every row
  isinConfirmed: boolean; // false: another security may answer for the ISIN (Obs 2, D-171)
  currency: string;
  quote_unit: QuoteUnit;
  asset_type: AssetType | null; // the provider's proposal; null: the user chooses
  exchanges: string[]; // information only: the listing is never chosen here (D-125)
};

/**
 * One row per result the source returned, with its symbol and exchange: rows of one ISIN are
 * not merged, since a source that does not carry the ISIN back may answer with another
 * security, which must not hide under the right name (Obs 2, D-171). Rows keep the provider's
 * order.
 */
export function groupResults(
  results: readonly SearchResult[],
  nameOf: (result: SearchResult & { exchange: string }) => string = (r) => r.exchange,
): SearchRow[] {
  const rows = new Map<string, SearchRow>();
  for (const result of results) {
    const key = result.isin ? `${result.isin} ${result.currency} ${result.symbol}` : result.symbol;
    const row = rows.get(key) ?? {
      key,
      name: "",
      isin: result.isin,
      ticker: result.symbol,
      symbol: result.symbol,
      isinConfirmed: Boolean(result.isin) && result.isin_confirmed,
      currency: result.currency,
      quote_unit: result.quote_unit,
      asset_type: null,
      exchanges: [],
    };
    if (!row.name && result.name) row.name = result.name;
    if (!row.asset_type && result.asset_type) row.asset_type = result.asset_type;
    const exchange = result.exchange ? nameOf({ ...result, exchange: result.exchange }) : null;
    if (exchange && !row.exchanges.includes(exchange)) row.exchanges.push(exchange);
    rows.set(key, row);
  }
  return [...rows.values()];
}
