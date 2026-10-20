// What the transaction form sends, from what was typed (D-139, D-140, D-141). Pure: no React,
// no request. Amounts stay strings; the server completes the gross and total amounts and
// checks everything again.

import type { Account, Asset, TransactionFields, TransactionType } from "../api/types";
import { type InputProblem, parseDecimalInput } from "../format/decimalInput";

/** The types the form offers (the others arrive in the next commit of slice 2). */
export const FORM_TYPES: readonly TransactionType[] = ["BUY", "SELL", "DEPOSIT", "WITHDRAWAL"];

export type Inputs = {
  type: TransactionType;
  account: string;
  asset: string;
  date: string; // YYYY-MM-DD from the date input, "" while incomplete
  time: string; // HH:MM, optional
  valueDate: string;
  quantity: string;
  price: string;
  gross: string; // typed over the computed one, or required for a foreign quote currency
  fees: string;
  accrued: string; // paid on a BUY, received on a SELL (bonds only)
  taxes: string; // signed
  amount: string; // DEPOSIT and WITHDRAWAL
  noCash: boolean;
  notes: string;
};

export const EMPTY_INPUTS: Inputs = {
  type: "BUY",
  account: "",
  asset: "",
  date: "",
  time: "",
  valueDate: "",
  quantity: "",
  price: "",
  gross: "",
  fees: "",
  accrued: "",
  taxes: "",
  amount: "",
  noCash: false,
  notes: "",
};

export type FieldName = Exclude<keyof Inputs, "type" | "noCash">;
export type Problem = InputProblem | "required";

const DECIMAL_FIELDS = [
  "quantity",
  "price",
  "gross",
  "fees",
  "accrued",
  "taxes",
  "amount",
] as const;
type DecimalField = (typeof DECIMAL_FIELDS)[number];

const TRADES: ReadonlySet<TransactionType> = new Set(["BUY", "SELL"]);
const CASH: ReadonlySet<TransactionType> = new Set(["DEPOSIT", "WITHDRAWAL"]);

export type Layout = {
  fields: ReadonlySet<FieldName>; // the fields shown for this type and security
  grossRequired: boolean; // quoted in another currency: the broker's converted amount
  cashOption: boolean; // "do not move cash"
};

/** Which fields the type has (domain-rules §2), given the security and the account. */
export function layoutOf(type: TransactionType, asset?: Asset, account?: Account): Layout {
  const common: FieldName[] = ["account", "date", "time", "valueDate", "notes"];
  if (CASH.has(type)) {
    return { fields: new Set([...common, "amount"]), grossRequired: false, cashOption: false };
  }
  const fields: FieldName[] = [...common, "asset", "quantity", "price", "gross", "fees"];
  const bond = asset?.asset_type === "BOND";
  if (bond) fields.push("accrued");
  if (type === "SELL" || bond) fields.push("taxes"); // BUY taxes are for bonds only (D-42)
  const grossRequired = !!asset && !!account && asset.quote_currency !== account.currency;
  return { fields: new Set(fields), grossRequired, cashOption: TRADES.has(type) };
}

export type Built = {
  body: TransactionFields | null; // null: something is missing or not a number
  problems: Partial<Record<FieldName, Problem>>;
  read: Partial<Record<DecimalField, string>>; // the numbers as read (D-140)
};

/** The request body for the input, or the problems that stop it. */
export function buildTransaction(inputs: Inputs, layout: Layout, locale: string): Built {
  const problems: Built["problems"] = {};
  const read: Built["read"] = {};
  const shown = (name: FieldName) => layout.fields.has(name);

  for (const name of DECIMAL_FIELDS) {
    if (!shown(name)) continue;
    const parsed = parseDecimalInput(inputs[name], locale, { signed: name === "taxes" });
    if (parsed.kind === "ok") read[name] = parsed.value;
    if (parsed.kind === "error") problems[name] = parsed.problem;
  }
  const require = (name: FieldName, present: boolean) => {
    if (!present && !problems[name]) problems[name] = "required";
  };
  require("account", inputs.account !== "");
  require("date", inputs.date !== "");
  if (shown("asset")) require("asset", inputs.asset !== "");
  if (shown("amount")) require("amount", read.amount !== undefined);
  if (shown("quantity")) {
    require("quantity", read.quantity !== undefined);
    if (layout.grossRequired) require("gross", read.gross !== undefined);
    // The gross is computed from the price unless it is typed.
    else require("price", read.price !== undefined || read.gross !== undefined);
  }
  if (Object.keys(problems).length > 0) return { body: null, problems, read };

  const body: TransactionFields = {
    account_id: inputs.account,
    type: inputs.type,
    trade_date: inputs.date,
  };
  if (inputs.time) body.trade_time = inputs.time;
  if (inputs.valueDate) body.value_date = inputs.valueDate;
  const notes = inputs.notes.trim();
  if (notes) body.notes = notes;
  if (CASH.has(inputs.type)) {
    body.total_amount = read.amount;
    return { body, problems, read };
  }
  body.asset_id = inputs.asset;
  body.quantity = read.quantity;
  if (read.price !== undefined) body.unit_price = read.price;
  if (read.gross !== undefined) body.gross_amount = read.gross;
  if (read.fees !== undefined) body.fees = read.fees;
  if (read.accrued !== undefined) {
    if (inputs.type === "BUY") body.accrued_interest_paid = read.accrued;
    else body.accrued_interest_received = read.accrued;
  }
  if (read.taxes !== undefined) body.taxes = read.taxes;
  if (layout.cashOption) body.affects_cash = !inputs.noCash;
  return { body, problems, read };
}

/** The form field where the server's issue on a request field is shown. */
export function fieldOfIssue(field: string, type: TransactionType): FieldName | null {
  switch (field) {
    case "account_id":
      return "account";
    case "asset_id":
      return "asset";
    case "trade_date":
      return "date";
    case "quantity":
      return "quantity";
    case "unit_price":
      return "price";
    case "gross_amount":
      return "gross";
    case "fees":
      return "fees";
    case "accrued_interest_paid":
    case "accrued_interest_received":
      return "accrued";
    case "taxes":
      return "taxes";
    case "total_amount":
      return CASH.has(type) ? "amount" : "gross"; // the total is never typed for a trade
    case "notes":
      return "notes";
    default:
      return null;
  }
}
