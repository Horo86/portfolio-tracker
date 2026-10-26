import { describe, expect, it } from "vitest";

import type { Account, Asset } from "../api/types";
import {
  buildBatch,
  emptyRow,
  readRow,
  rowFieldOfIssue,
  type RowInput,
  sameAssetRow,
} from "./addExisting";

// Invented accounts, securities and amounts only.
const EUR: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 0,
  currency_locked: false,
};
const WORLD: Asset = {
  id: "00000000-0000-4000-8000-0000000000b1",
  isin: null,
  ticker: null,
  name: "Mondo Azionario ETF",
  asset_type: "ETF",
  quote_currency: "EUR",
  quote_unit: "CURRENCY",
  shared: false,
  in_use: false,
  created_at: "2026-01-10T09:00:00Z",
};
const ROBOTICS: Asset = { ...WORLD, id: "00000000-0000-4000-8000-0000000000b2", quote_currency: "USD" };
const ASSETS = [WORLD, ROBOTICS];

const row = (key: number, extra: Partial<RowInput>): RowInput => ({ ...emptyRow(key), ...extra });

describe("readRow", () => {
  it("ignores an empty row and reads a complete one per language", () => {
    expect(readRow(emptyRow(1), undefined, EUR, "it")).toMatchObject({ empty: true, fields: null });
    const state = readRow(row(1, { asset: WORLD.id, quantity: "19", price: "102,5" }), WORLD, EUR, "it");
    expect(state).toEqual({
      empty: false,
      foreign: false,
      problems: {},
      fields: { asset_id: WORLD.id, quantity: "19", unit_price: "102.5", affects_cash: false },
    });
  });

  it("names what a half-filled or unreadable row lacks", () => {
    expect(readRow(row(1, { quantity: "19" }), undefined, EUR, "en").problems).toEqual({
      asset: "required",
      price: "required",
    });
    expect(
      readRow(row(1, { asset: WORLD.id, quantity: "1.123456789", price: "abc" }), WORLD, EUR, "en")
        .problems,
    ).toEqual({ quantity: "tooManyDecimals", price: "invalid" });
    expect(readRow(row(1, { asset: WORLD.id, quantity: "-3", price: "1" }), WORLD, EUR, "en").problems)
      .toEqual({ quantity: "negative" });
  });

  it("asks a security in another currency for its total in the account currency", () => {
    const half = readRow(row(1, { asset: ROBOTICS.id, quantity: "30", price: "94.10" }), ROBOTICS, EUR, "en");
    expect(half).toMatchObject({ foreign: true, problems: { total: "required" }, fields: null });
    const full = readRow(
      row(1, { asset: ROBOTICS.id, quantity: "30", price: "94.10", total: "2,595.00" }),
      ROBOTICS,
      EUR,
      "en",
    );
    expect(full.fields).toEqual({
      asset_id: ROBOTICS.id,
      quantity: "30",
      unit_price: "94.10",
      gross_amount: "2595.00",
      affects_cash: false,
    });
  });

  it("ignores a total left over in a row whose security is in the account currency", () => {
    const state = readRow(row(1, { asset: WORLD.id, quantity: "1", price: "1", total: "x" }), WORLD, EUR, "en");
    expect(state.fields).not.toHaveProperty("gross_amount");
    expect(state.problems).toEqual({});
  });
});

describe("buildBatch", () => {
  it("sends the complete rows as BUYs that do not move cash, and maps them back", () => {
    const rows = [
      row(1, { asset: WORLD.id, quantity: "19", price: "102" }),
      emptyRow(2),
      row(3, { asset: ROBOTICS.id, quantity: "30", price: "94.10", total: "2595" }),
    ];
    const batch = buildBatch(rows, ASSETS, EUR, "2025-12-31", "en");
    expect(batch.body).toEqual({
      transactions: [
        {
          account_id: EUR.id,
          type: "BUY",
          trade_date: "2025-12-31",
          asset_id: WORLD.id,
          quantity: "19",
          unit_price: "102",
          affects_cash: false,
        },
        {
          account_id: EUR.id,
          type: "BUY",
          trade_date: "2025-12-31",
          asset_id: ROBOTICS.id,
          quantity: "30",
          unit_price: "94.10",
          gross_amount: "2595",
          affects_cash: false,
        },
      ],
    });
    expect(batch.rowOf).toEqual([0, 2]);
    expect(batch.incomplete).toBe(0);
  });

  it("counts half-filled rows, and sends nothing without an account, a date or a row", () => {
    const rows = [row(1, { asset: WORLD.id, quantity: "19", price: "102" }), row(2, { price: "5" })];
    expect(buildBatch(rows, ASSETS, EUR, "2025-12-31", "en")).toMatchObject({
      incomplete: 1,
      rowOf: [0],
    });
    expect(buildBatch(rows, ASSETS, undefined, "2025-12-31", "en").body).toBeNull();
    expect(buildBatch(rows, ASSETS, EUR, "", "en").body).toBeNull();
    expect(buildBatch([emptyRow(1)], ASSETS, EUR, "2025-12-31", "en")).toMatchObject({
      body: null,
      incomplete: 0,
    });
  });
});

describe("row helpers", () => {
  it("finds another row with the same security", () => {
    const rows = [row(1, { asset: WORLD.id }), emptyRow(2), row(3, { asset: WORLD.id })];
    expect(sameAssetRow(rows, 0)).toBe(2);
    expect(sameAssetRow(rows, 2)).toBe(0);
    expect(sameAssetRow(rows, 1)).toBeNull();
  });

  it("places the server's issues on the row's fields", () => {
    expect(rowFieldOfIssue("unit_price")).toBe("price");
    expect(rowFieldOfIssue("gross_amount")).toBe("total");
    expect(rowFieldOfIssue("total_amount")).toBe("total");
    expect(rowFieldOfIssue("trade_date")).toBeNull();
  });
});
