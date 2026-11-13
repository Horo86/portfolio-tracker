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
  isin_confirmed: false,
  isin: ISIN,
  ...extra,
});

describe("groupResults", () => {
  it("makes one row per result, with its symbol and exchange, never merging an ISIN's rows (Obs 2)", () => {
    const rows = groupResults([
      line({}),
      line({ symbol: "ZWLD.DE", exchange: "XETRA" }),
      line({ symbol: "ZWLD.L", exchange: "London", currency: "GBP", quote_unit: "MINOR_CURRENCY_UNIT" }),
      line({ symbol: "OTHR.F", exchange: "XETRA", name: "Another Security" }), // another answer
    ]);
    expect(rows.map((r) => [r.key, r.name, r.symbol, r.exchanges, r.currency])).toEqual([
      [`${ISIN} EUR ZWLD.MI`, "Esempio Mondo ETF", "ZWLD.MI", ["Milan"], "EUR"],
      [`${ISIN} EUR ZWLD.DE`, "Esempio Mondo ETF", "ZWLD.DE", ["XETRA"], "EUR"],
      [`${ISIN} GBP ZWLD.L`, "Esempio Mondo ETF", "ZWLD.L", ["London"], "GBP"],
      [`${ISIN} EUR OTHR.F`, "Another Security", "OTHR.F", ["XETRA"], "EUR"],
    ]);
    // The symbol is the ticker on an ISIN row too (D-175 (3)).
    expect(rows[0]).toMatchObject({ isin: ISIN, ticker: "ZWLD.MI", isinConfirmed: false });
  });

  it("confirms an ISIN row only when the source carried the ISIN back", () => {
    const [confirmed] = groupResults([line({ isin_confirmed: true })]);
    expect(confirmed.isinConfirmed).toBe(true);
    const [text] = groupResults([line({ isin: null, isin_confirmed: true })]);
    expect(text.isinConfirmed).toBe(false);
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
