import { describe, expect, it } from "vitest";

import type { Account, Asset, Transaction } from "../api/types";
import {
  assetChoices,
  buildTransaction,
  EMPTY_INPUTS,
  fieldOfIssue,
  type Inputs,
  inputsOf,
  keptFields,
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

  it("gives income its gross and taxes, a dividend its informational quantity (D-141)", () => {
    const dividend = layoutOf("DIVIDEND", etf(), EUR);
    expect(dividend.kind).toBe("income");
    expect([...dividend.fields].sort()).toEqual([
      "account", "asset", "date", "gross", "notes", "quantity", "taxes", "time", "valueDate",
    ]);
    expect(dividend.cashOption).toBe(true);
    const coupon = layoutOf("COUPON", BOND, EUR);
    expect(coupon.fields.has("quantity")).toBe(false);
    expect(coupon.fields.has("accrued")).toBe(false);
    // An income's gross is always typed, whatever the security's currency.
    expect(layoutOf("DIVIDEND", USD_ETF, EUR).grossRequired).toBe(false);
  });

  it("gives a split its ratio and no cash option", () => {
    const split = layoutOf("STOCK_SPLIT", etf(), EUR);
    expect([...split.fields].sort()).toEqual([
      "account", "asset", "date", "notes", "splitNew", "splitOld", "time", "valueDate",
    ]);
    expect(split.cashOption).toBe(false);
  });
});

describe("assetChoices", () => {
  it("offers only bonds for a coupon", () => {
    const all = [etf(), BOND, USD_ETF];
    expect(assetChoices("COUPON", all)).toEqual([BOND]);
    expect(assetChoices("DIVIDEND", all)).toEqual(all);
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
      {
        account: "",
        date: "",
        quantity: "1,2,3",
        price: "0,123456789012345678901",
        fees: "-1",
        taxes: "-1",
      },
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

  it("takes 8 decimals in a quantity, as many as the server in a price or amount (D-140)", () => {
    expect(build({ asset: "etf", quantity: "1,123456789", price: "10" }).problems).toEqual({
      quantity: "tooManyDecimals",
    });
    const { body } = build({
      asset: "etf",
      quantity: "3",
      price: "4,1893000680",
      gross: "12,5679002040",
    });
    expect(body).toMatchObject({ unit_price: "4.1893000680", gross_amount: "12.5679002040" });
  });

  it("sends an income's gross and taxes and leaves its total to the server", () => {
    const typed = { type: "DIVIDEND" as const, asset: "etf", gross: "52,50", taxes: "13,65" };
    expect(build(typed).body).toEqual({
      account_id: EUR.id,
      type: "DIVIDEND",
      trade_date: "2026-10-12",
      asset_id: "etf",
      gross_amount: "52.50",
      taxes: "13.65",
      affects_cash: true,
    });
    expect(build({ ...typed, quantity: "120" }).body?.quantity).toBe("120");
    // Taxes withheld on income are never negative (domain-rules §2).
    expect(build({ ...typed, taxes: "-1" }).problems).toEqual({ taxes: "negative" });
    expect(build({ type: "COUPON", asset: "btp" }, BOND).problems).toEqual({ gross: "required" });
  });

  it("sends a split as its new:old ratio, each part read per language", () => {
    const { body } = build({ type: "STOCK_SPLIT", asset: "etf", splitNew: "3", splitOld: "1,5" });
    expect(body).toEqual({
      account_id: EUR.id,
      type: "STOCK_SPLIT",
      trade_date: "2026-10-12",
      asset_id: "etf",
      split_ratio: "3:1.5",
    });
    expect(build({ type: "STOCK_SPLIT", asset: "etf", splitNew: "2" }).problems).toEqual({
      splitOld: "required",
    });
  });
});

describe("inputsOf", () => {
  const stored = {
    id: "tx",
    account_id: EUR.id,
    type: "BUY",
    trade_date: "2026-09-05",
    trade_time: "10:15:00",
    value_date: null,
    asset_id: "btp",
    quantity: "5000",
    unit_price: "97.5",
    gross_amount: "4875.00",
    fees: "3.00",
    accrued_interest_paid: "12.34",
    accrued_interest_received: null,
    taxes: "-2.44",
    total_amount: "4887.90",
    split_ratio: null,
    affects_cash: true,
    external_ref: "ORD-1",
    metadata: { source: "csv" },
    notes: null,
  } as unknown as Transaction;

  it("writes the stored numbers in the language and keeps the stored gross", () => {
    const inputs = inputsOf(stored, "it");
    expect(inputs).toMatchObject({
      type: "BUY", asset: "btp", date: "2026-09-05", time: "10:15:00", quantity: "5000",
      price: "97,5", gross: "4875,00", fees: "3,00", accrued: "12,34", taxes: "-2,44",
      amount: "", noCash: false, notes: "",
    });
    // Saved again unchanged: the same amounts, the total left to the server.
    const { body } = buildTransaction(inputs, layoutOf("BUY", BOND, EUR), "it");
    expect(body).toMatchObject({
      quantity: "5000", unit_price: "97.5", gross_amount: "4875.00", fees: "3.00",
      accrued_interest_paid: "12.34", taxes: "-2.44", trade_time: "10:15:00",
    });
  });

  it("reads a cash movement's total and a split's ratio back into their fields", () => {
    const deposit = { ...stored, type: "DEPOSIT", asset_id: null, total_amount: "2000.00" };
    expect(inputsOf(deposit as Transaction, "en")).toMatchObject({ amount: "2000.00", gross: "" });
    const split = { ...stored, type: "STOCK_SPLIT", split_ratio: "1:10" } as Transaction;
    expect(inputsOf(split, "en")).toMatchObject({ splitNew: "1", splitOld: "10" });
  });

  it("keeps an import's reference and metadata, which the form does not show", () => {
    expect(keptFields(stored)).toEqual({ external_ref: "ORD-1", metadata: { source: "csv" } });
    expect(keptFields({ ...stored, external_ref: null, metadata: null })).toEqual({});
  });
});

describe("fieldOfIssue", () => {
  it("shows a server issue next to the field that caused it", () => {
    expect(fieldOfIssue("unit_price", "BUY")).toBe("price");
    expect(fieldOfIssue("accrued_interest_received", "SELL")).toBe("accrued");
    expect(fieldOfIssue("total_amount", "DEPOSIT")).toBe("amount");
    expect(fieldOfIssue("total_amount", "BUY")).toBe("gross"); // never typed for a trade
    expect(fieldOfIssue("total_amount", "DIVIDEND")).toBe("gross");
    expect(fieldOfIssue("split_ratio", "STOCK_SPLIT")).toBe("splitNew");
    expect(fieldOfIssue("external_ref", "BUY")).toBeNull();
  });
});
