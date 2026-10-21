// What the transaction form sends, from what was typed (D-139, D-140, D-141). Pure: no React,
// no request. Amounts stay strings; the server completes the gross and total amounts and
// checks everything again.

import type {
  Account,
  Asset,
  Transaction,
  TransactionFields,
  TransactionType,
} from "../api/types";
import {
  decimalToInput,
  type InputProblem,
  MAX_AMOUNT_DECIMALS,
  MAX_QUANTITY_DECIMALS,
  parseDecimalInput,
} from "../format/decimalInput";

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
  splitNew: string; // STOCK_SPLIT, new:old
  splitOld: string;
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
  splitNew: "",
  splitOld: "",
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
  "splitNew",
  "splitOld",
] as const;
type DecimalField = (typeof DECIMAL_FIELDS)[number];

const QUANTITY_FIELDS: ReadonlySet<FieldName> = new Set(["quantity", "splitNew", "splitOld"]);

/** How many decimals a field takes (D-140): quantities 8 (D-68), prices and amounts as many
 * as the server (D-98). */
export function maxDecimalsOf(name: FieldName): number {
  return QUANTITY_FIELDS.has(name) ? MAX_QUANTITY_DECIMALS : MAX_AMOUNT_DECIMALS;
}

/** The shape of the form: trades, income, cash movements and splits (domain-rules §2). */
export type Kind = "trade" | "income" | "cash" | "split";

const KINDS: Record<TransactionType, Kind> = {
  BUY: "trade",
  SELL: "trade",
  DIVIDEND: "income",
  COUPON: "income",
  DEPOSIT: "cash",
  WITHDRAWAL: "cash",
  STOCK_SPLIT: "split",
};

export type Layout = {
  kind: Kind;
  fields: ReadonlySet<FieldName>; // the fields shown for this type and security
  grossRequired: boolean; // a trade quoted in another currency: the broker's converted amount
  cashOption: boolean; // "do not move cash"
};

/** Which fields the type has (domain-rules §2), given the security and the account. */
export function layoutOf(type: TransactionType, asset?: Asset, account?: Account): Layout {
  const kind = KINDS[type];
  const common: FieldName[] = ["account", "date", "time", "valueDate", "notes"];
  const layout = (fields: FieldName[], cashOption: boolean, grossRequired = false): Layout => ({
    kind,
    fields: new Set([...common, ...fields]),
    grossRequired,
    cashOption,
  });
  if (kind === "cash") return layout(["amount"], false);
  if (kind === "split") return layout(["asset", "splitNew", "splitOld"], false);
  // DIVIDEND keeps its informational quantity (D-141); taxes withheld are never negative.
  if (kind === "income") {
    const fields: FieldName[] = ["asset", "gross", "taxes"];
    if (type === "DIVIDEND") fields.push("quantity");
    return layout(fields, true);
  }
  const fields: FieldName[] = ["asset", "quantity", "price", "gross", "fees"];
  const bond = asset?.asset_type === "BOND";
  if (bond) fields.push("accrued");
  if (type === "SELL" || bond) fields.push("taxes"); // BUY taxes are for bonds only (D-42)
  const foreign = !!asset && !!account && asset.quote_currency !== account.currency;
  return layout(fields, true, foreign);
}

/** The securities the type can refer to: coupons are paid by bonds only. */
export function assetChoices(type: TransactionType, assets: readonly Asset[]): Asset[] {
  return type === "COUPON" ? assets.filter((a) => a.asset_type === "BOND") : [...assets];
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
    // Taxes are signed on trades (a credit is negative); income taxes are withheld (≥ 0).
    const signed = name === "taxes" && layout.kind === "trade";
    const parsed = parseDecimalInput(inputs[name], locale, {
      signed,
      maxDecimals: maxDecimalsOf(name),
    });
    if (parsed.kind === "ok") read[name] = parsed.value;
    if (parsed.kind === "error") problems[name] = parsed.problem;
  }
  const require = (name: FieldName, present: boolean) => {
    if (!present && !problems[name]) problems[name] = "required";
  };
  require("account", inputs.account !== "");
  require("date", inputs.date !== "");
  if (shown("asset")) require("asset", inputs.asset !== "");
  if (layout.kind === "cash") require("amount", read.amount !== undefined);
  if (layout.kind === "income") require("gross", read.gross !== undefined);
  if (layout.kind === "split") {
    require("splitNew", read.splitNew !== undefined);
    require("splitOld", read.splitOld !== undefined);
  }
  if (layout.kind === "trade") {
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
  if (layout.kind === "cash") {
    body.total_amount = read.amount;
    return { body, problems, read };
  }
  body.asset_id = inputs.asset;
  if (layout.kind === "split") {
    body.split_ratio = `${read.splitNew}:${read.splitOld}`;
    return { body, problems, read };
  }
  if (read.quantity !== undefined) body.quantity = read.quantity;
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
      // The total is typed only for cash movements; otherwise it follows the gross.
      return KINDS[type] === "cash" ? "amount" : "gross";
    case "split_ratio":
      return "splitNew";
    case "notes":
      return "notes";
    default:
      return null;
  }
}

/** The form's input for a stored transaction, numbers written in the language (edit page).
 * The stored gross is kept as typed: an edit that changes nothing of it saves it unchanged. */
export function inputsOf(tx: Transaction, locale: string): Inputs {
  const text = (value: string | null | undefined) =>
    value === null || value === undefined ? "" : decimalToInput(value, locale);
  const cash = KINDS[tx.type] === "cash";
  const [splitNew, splitOld] = tx.split_ratio?.split(":") ?? [];
  return {
    type: tx.type,
    account: tx.account_id,
    asset: tx.asset_id ?? "",
    date: tx.trade_date,
    time: tx.trade_time ?? "",
    valueDate: tx.value_date ?? "",
    quantity: text(tx.quantity),
    price: text(tx.unit_price),
    gross: cash ? "" : text(tx.gross_amount),
    fees: text(tx.fees),
    accrued: text(tx.accrued_interest_paid ?? tx.accrued_interest_received),
    taxes: text(tx.taxes),
    amount: cash ? text(tx.total_amount) : "",
    splitNew: text(splitNew),
    splitOld: text(splitOld),
    noCash: tx.affects_cash === false,
    notes: tx.notes ?? "",
  };
}

/** Stored fields the form does not show, sent back unchanged on an edit (an import's
 * reference and metadata). */
export function keptFields(tx: Transaction): Partial<TransactionFields> {
  return {
    ...(tx.external_ref !== null ? { external_ref: tx.external_ref } : {}),
    ...(tx.metadata !== null ? { metadata: tx.metadata } : {}),
  };
}
