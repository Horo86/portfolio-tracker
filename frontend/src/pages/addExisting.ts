// "Add holdings I already own" (mockup 10, D-147): its rows as the batch they become. Pure:
// no React, no request. Amounts stay strings; the server's dry run completes each row.

import type { Account, Asset, BatchIn, TransactionFields } from "../api/types";
import {
  type InputProblem,
  MAX_AMOUNT_DECIMALS,
  MAX_QUANTITY_DECIMALS,
  parseDecimalInput,
} from "../format/decimalInput";

export const MAX_ROWS = 50; // D-147, as the server checks it
export const START_ROWS = 3;

export type RowInput = {
  key: number; // stable while rows are added and removed
  asset: string;
  quantity: string; // the nominal for a bond
  price: string; // the average cost, % of nominal for a bond
  total: string; // only for a security quoted in another currency: the broker's cost
};

export type RowField = "asset" | "quantity" | "price" | "total";
export type RowProblem = InputProblem | "required";

export type RowState = {
  empty: boolean; // nothing typed: ignored
  foreign: boolean; // quoted in another currency than the account: the total is asked for
  problems: Partial<Record<RowField, RowProblem>>;
  fields: Omit<TransactionFields, "account_id" | "trade_date" | "type"> | null; // null: not complete
};

export const emptyRow = (key: number, asset = ""): RowInput => ({
  key,
  asset,
  quantity: "",
  price: "",
  total: "",
});

/** Whether a row's security is quoted in another currency than the account (D-139: the
 * server never converts, so the broker's cost in the account currency is typed). */
export function isForeign(asset: Asset | undefined, account: Account | undefined): boolean {
  return !!asset && !!account && asset.quote_currency !== account.currency;
}

/** One row read per language (D-140): what it sends, or what stops it. */
export function readRow(
  row: RowInput,
  asset: Asset | undefined,
  account: Account | undefined,
  locale: string,
): RowState {
  const foreign = isForeign(asset, account);
  const typed = [row.asset, row.quantity, row.price, foreign ? row.total : ""];
  const empty = typed.every((value) => value.trim() === "");
  const problems: RowState["problems"] = {};
  const read: Partial<Record<RowField, string>> = {};
  const decimals: [RowField, number][] = [
    ["quantity", MAX_QUANTITY_DECIMALS],
    ["price", MAX_AMOUNT_DECIMALS],
    ...(foreign ? ([["total", MAX_AMOUNT_DECIMALS]] as [RowField, number][]) : []),
  ];
  for (const [name, maxDecimals] of decimals) {
    const parsed = parseDecimalInput(row[name], locale, { maxDecimals });
    if (parsed.kind === "ok") read[name] = parsed.value;
    else if (parsed.kind === "error") problems[name] = parsed.problem;
    else problems[name] = "required";
  }
  if (!asset) problems.asset = "required";
  if (empty || Object.keys(problems).length > 0) return { empty, foreign, problems, fields: null };
  return {
    empty,
    foreign,
    problems,
    fields: {
      asset_id: row.asset,
      quantity: read.quantity,
      unit_price: read.price,
      ...(foreign ? { gross_amount: read.total } : {}),
      affects_cash: false,
    },
  };
}

export type Batch = {
  states: RowState[]; // per row on screen
  body: BatchIn | null; // the complete rows; null: none, or no account or date
  rowOf: number[]; // the screen row of each transaction of `body`
  incomplete: number; // rows half filled or not readable: they block Save
};

/** The batch of the screen: every complete row becomes a BUY that does not move cash, on the
 * one date, in the one account; empty rows are ignored. */
export function buildBatch(
  rows: readonly RowInput[],
  assets: readonly Asset[],
  account: Account | undefined,
  date: string,
  locale: string,
): Batch {
  const states = rows.map((row) =>
    readRow(row, assets.find((a) => a.id === row.asset), account, locale),
  );
  const transactions: TransactionFields[] = [];
  const rowOf: number[] = [];
  states.forEach((state, index) => {
    if (!state.fields || !account || !date) return;
    transactions.push({ account_id: account.id, type: "BUY", trade_date: date, ...state.fields });
    rowOf.push(index);
  });
  return {
    states,
    body: transactions.length > 0 ? { transactions } : null,
    rowOf,
    incomplete: states.filter((s) => !s.empty && !s.fields).length,
  };
}

/** The earlier row with the same security, for the hint "also in row N" (allowed, D-147). */
export function sameAssetRow(rows: readonly RowInput[], index: number): number | null {
  const asset = rows[index].asset;
  if (!asset) return null;
  const earlier = rows.findIndex((row, i) => i !== index && row.asset === asset);
  return earlier === -1 ? null : earlier;
}

/** The row field where the server's issue on a request field is shown; null: not a row's. */
export function rowFieldOfIssue(field: string): RowField | null {
  switch (field) {
    case "asset_id":
      return "asset";
    case "quantity":
      return "quantity";
    case "unit_price":
      return "price";
    case "gross_amount":
    case "total_amount":
      return "total";
    default:
      return null;
  }
}
