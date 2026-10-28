import { describe, expect, it } from "vitest";

import type { SearchResult } from "./api/types";
import { groupResults } from "./assetSearch";

// Invented ISINs, names and symbols only.
const ISIN = "ZZ0000087013";
const line = (extra: Partial<SearchResult>): SearchResult => ({
  name: "Esempio Mondo ETF",
  symbol: "ZWLD.MI",
  exchange: "Milan",
  exchange_code: null,
  currency: "EUR",
  quote_unit: "CURRENCY",
  asset_type: "ETF",
  isin: ISIN,
  ...extra,
});

describe("groupResults", () => {
  it("makes one row per ISIN and currency, with its exchanges", () => {
    const rows = groupResults([
      line({}),
      line({ symbol: "ZWLD.DE", exchange: "XETRA" }),
      line({ symbol: "ZWLD.L", exchange: "London", currency: "GBP", quote_unit: "MINOR_CURRENCY_UNIT" }),
      line({ symbol: "ZWLD.F", exchange: "XETRA" }),
    ]);
    expect(rows).toEqual([
      {
        key: `${ISIN} EUR`,
        name: "Esempio Mondo ETF",
        isin: ISIN,
        ticker: null,
        currency: "EUR",
        quote_unit: "CURRENCY",
        asset_type: "ETF",
        exchanges: ["Milan", "XETRA"],
      },
      {
        key: `${ISIN} GBP`,
        name: "Esempio Mondo ETF",
        isin: ISIN,
        ticker: null,
        currency: "GBP",
        quote_unit: "MINOR_CURRENCY_UNIT",
        asset_type: "ETF",
        exchanges: ["London"],
      },
    ]);
  });

  it("takes the first name and type a listing gives, and leaves out unnamed exchanges", () => {
    const [row] = groupResults([
      line({ name: null, asset_type: null, exchange: null }),
      line({ name: "Esempio Mondo", asset_type: "ETF", exchange: "Milan" }),
    ]);
    expect(row).toMatchObject({ name: "Esempio Mondo", asset_type: "ETF", exchanges: ["Milan"] });
    const [unnamed] = groupResults([line({ name: null, asset_type: null })]);
    expect(unnamed).toMatchObject({ name: "", asset_type: null });
  });

  it("keeps each result of a text search apart, with its symbol as the ticker", () => {
    const rows = groupResults([
      line({ isin: null, symbol: "ZTXT.MI", name: "Esempio Testo SpA", asset_type: "STOCK" }),
      line({ isin: null, symbol: "ZTXT.DE", name: "Esempio Testo SpA", asset_type: "STOCK", exchange: "XETRA" }),
    ]);
    expect(rows.map((r) => [r.key, r.ticker, r.isin, r.exchanges])).toEqual([
      ["ZTXT.MI", "ZTXT.MI", null, ["Milan"]],
      ["ZTXT.DE", "ZTXT.DE", null, ["XETRA"]],
    ]);
  });
});
