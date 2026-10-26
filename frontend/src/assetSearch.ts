// The search of screen 11 (D-128, D-148): the provider's results as the page shows them.
// Pure: no React, no request.

import type { AssetType, QuoteUnit, SearchResult } from "./api/types";

/** One row of the results: what "Aggiungi" puts into the create form. */
export type SearchRow = {
  key: string;
  name: string; // "" when no listing named it
  isin: string | null; // null: a text search, manual prices only
  ticker: string | null; // the provider's symbol, for a text result only
  currency: string;
  quote_unit: QuoteUnit;
  asset_type: AssetType | null; // the provider's proposal; null: the user chooses
  exchanges: string[]; // information only: the listing is never chosen here (D-125)
};

/**
 * One row per ISIN and currency, since that pair is all adding sends (the ui README, Q-14),
 * with the exchanges of its listings; each result of a text search is a row of its own.
 * Rows keep the provider's order.
 */
export function groupResults(results: readonly SearchResult[]): SearchRow[] {
  const rows = new Map<string, SearchRow>();
  for (const result of results) {
    const key = result.isin ? `${result.isin} ${result.currency}` : result.symbol;
    const row = rows.get(key) ?? {
      key,
      name: "",
      isin: result.isin,
      ticker: result.isin ? null : result.symbol,
      currency: result.currency,
      quote_unit: result.quote_unit,
      asset_type: null,
      exchanges: [],
    };
    if (!row.name && result.name) row.name = result.name;
    if (!row.asset_type && result.asset_type) row.asset_type = result.asset_type;
    if (result.exchange && !row.exchanges.includes(result.exchange)) {
      row.exchanges.push(result.exchange);
    }
    rows.set(key, row);
  }
  return [...rows.values()];
}
