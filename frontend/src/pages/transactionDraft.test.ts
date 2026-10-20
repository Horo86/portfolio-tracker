import { describe, expect, it } from "vitest";

import type { Account, Asset } from "../api/types";
import {
  buildTransaction,
  EMPTY_INPUTS,
  fieldOfIssue,
  type Inputs,
  layoutOf,
} from "./transactionDraft";

// Invented accounts and securities only.
const EUR = { id: "acc-eur", currency: "EUR" } as Account;
const etf = (extra: Partial<Asset> = {}) =>
  ({
    id: "etf",
    asset_type: "ETF",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
    ...extra,
  }) as Asset;
const BOND = etf({ id: "btp", asset_type: "BOND", quote_unit: "PERCENT_OF_NOMINAL" });
const USD_ETF = etf({ id: "usd", quote_currency: "USD" });

const build = (inputs: Partial<Inputs>, asset: Asset | undefined = etf(), locale = "it") => {
  const all = { ...EMPTY_INPUTS, account: EUR.id, date: "2026-10-12", ...inputs };
  return buildTransaction(all, layoutOf(all.type, asset, EUR), locale);
};

describe("layoutOf", () => {
  it("gives each type its fields (domain-rules §2)", () => {
    const buy = layoutOf("BUY", etf(), EUR);
    expect([...buy.fields].sort()).toEqual([
      "account", "asset", "date", "fees", "gross", "notes", "price", "quantity", "time",
      "valueDate",
    ]);
    expect(buy.cashOption).toBe(true);
    expect(layoutOf("SELL", etf(), EUR).fields.has("taxes")).toBe(true);
    // Accrued interest and BUY taxes only for bonds (D-42).
    expect(layoutOf("BUY", BOND, EUR).fields.has("accrued")).toBe(true);
    expect(layoutOf("BUY", BOND, EUR).fields.has("taxes")).toBe(true);
    const deposit = layoutOf("DEPOSIT", undefined, EUR);
    expect([...deposit.fields].sort()).toEqual([
      "account", "amount", "date", "notes", "time", "valueDate",
    ]);
    expect(deposit.cashOption).toBe(false);
  });

  it("asks for the broker's gross when the security is quoted in another currency", () => {
    expect(layoutOf("BUY", USD_ETF, EUR).grossRequired).toBe(true);
    expect(layoutOf("BUY", etf(), EUR).grossRequired).toBe(false);
  });
});

describe("buildTransaction", () => {
  it("sends what was typed, read per language, and leaves the amounts to the server", () => {
    const typed = { asset: "etf", quantity: "15", price: "101,80", fees: "3,00" };
    const { body, problems } = build(typed);
    expect(problems).toEqual({});
    expect(body).toEqual({
      account_id: EUR.id,
      type: "BUY",
      trade_date: "2026-10-12",
      asset_id: "etf",
      quantity: "15",
      unit_price: "101.80",
      fees: "3.00",
      affects_cash: true,
    });
  });

  it("reads 1.234 as thousands in Italian and shows it back", () => {
    const { body, read } = build({ asset: "etf", quantity: "1.234", price: "1.5" });
    expect(read).toEqual({ quantity: "1234", price: "1.5" });
    expect(body?.quantity).toBe("1234");
  });

  it("puts accrued interest where the type wants it: paid on a BUY, received on a SELL", () => {
    const inputs = {
      asset: "btp", quantity: "5.000", price: "97,50", accrued: "12,34", taxes: "-2,44",
    };
    const buy = build(inputs, BOND).body;
    expect(buy).toMatchObject({
      accrued_interest_paid: "12.34", taxes: "-2.44", quantity: "5000",
    });
    expect(buy).not.toHaveProperty("accrued_interest_received");
    const sell = build({ ...inputs, type: "SELL", taxes: "3,13" }, BOND).body;
    expect(sell).toMatchObject({ accrued_interest_received: "12.34", taxes: "3.13" });
  });

  it("never sends a field the type or the security does not have (D-62)", () => {
    // Left over from a bond, or typed before switching type.
    const typed = {
      asset: "etf", quantity: "1", price: "2", accrued: "5", taxes: "1", amount: "9",
    };
    expect(build(typed).body).not.toHaveProperty("accrued_interest_paid");
    expect(build(typed).body).not.toHaveProperty("taxes");
    expect(build(typed).body).not.toHaveProperty("total_amount");
    const deposit = build({ ...typed, type: "DEPOSIT" }).body;
    expect(deposit).toEqual({
      account_id: EUR.id,
      type: "DEPOSIT",
      trade_date: "2026-10-12",
      total_amount: "9",
    });
  });

  it("sends the optional time, value date and notes, and the cash option", () => {
    const { body } = build({
      type: "SELL", asset: "etf", quantity: "1", price: "2", time: "14:30",
      valueDate: "2026-10-14", notes: "  piano di accumulo  ", noCash: true,
    });
    expect(body).toMatchObject({
      trade_time: "14:30",
      value_date: "2026-10-14",
      notes: "piano di accumulo",
      affects_cash: false,
    });
  });

  it("needs a price, or a typed gross that replaces it", () => {
    expect(build({ asset: "etf", quantity: "1" }).problems).toEqual({ price: "required" });
    expect(build({ asset: "etf", quantity: "1", gross: "10" }).body?.gross_amount).toBe("10");
  });

  it("needs the broker's gross for a foreign quote currency; the price is then optional", () => {
    expect(build({ asset: "usd", quantity: "10", price: "140,20" }, USD_ETF).problems).toEqual({
      gross: "required",
    });
    const { body } = build({ asset: "usd", quantity: "10", gross: "1291,10" }, USD_ETF);
    expect(body).toMatchObject({ gross_amount: "1291.10" });
    expect(body).not.toHaveProperty("unit_price");
  });

  it("names what is missing and what is not a number", () => {
    const { body, problems } = build(
      { account: "", date: "", quantity: "1,2,3", price: "0,123456789", fees: "-1", taxes: "-1" },
      etf(),
    );
    expect(body).toBeNull();
    expect(problems).toEqual({
      account: "required",
      date: "required",
      asset: "required",
      quantity: "invalid",
      price: "tooManyDecimals",
      fees: "negative",
    });
    expect(build({ type: "WITHDRAWAL" }).problems).toEqual({ amount: "required" });
  });
});

describe("fieldOfIssue", () => {
  it("shows a server issue next to the field that caused it", () => {
    expect(fieldOfIssue("unit_price", "BUY")).toBe("price");
    expect(fieldOfIssue("accrued_interest_received", "SELL")).toBe("accrued");
    expect(fieldOfIssue("total_amount", "DEPOSIT")).toBe("amount");
    expect(fieldOfIssue("total_amount", "BUY")).toBe("gross"); // never typed for a trade
    expect(fieldOfIssue("split_ratio", "BUY")).toBeNull();
  });
});
