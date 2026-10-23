import type { TFunction } from "i18next";

import type { ImportFormat, ImportReason } from "./api/types";
import { issueText } from "./errors";
import { formatDay } from "./format/date";
import { formatMoney, formatQuantity } from "./format/decimal";

// The texts of the import preview's codes (backend `importers/codes.py`; D-142). Every code
// has a text in it.json and en.json (checked by a backend test); an unknown code is named,
// never hidden. Parameters are file values (a movement type, a column name, a row number)
// or figures, which are formatted for the language here; the server's `detail` is never
// shown.

export type TextContext = {
  t: TFunction;
  locale: string;
  format: ImportFormat; // a CSV field is named as its column; a Directa one is translated
  currency: string; // the amounts of a line are in its account's currency
  assetName: (key: string) => string;
  accountName: (key: string) => string;
  rowsOf: (lineKey: string) => number[]; // the file rows of a preview line
};

/** "row 5" or "rows 30, 31". */
export function rowsText(t: TFunction, rows: readonly number[]): string {
  if (rows.length === 1) return t("importCodes.row", { row: rows[0] });
  return t("importCodes.rows", { rows: [...rows].sort((a, b) => a - b).join(", ") });
}

function known(t: TFunction, section: string, code: string): boolean {
  return t(`${section}.${code}`, { defaultValue: "" }) !== "";
}

function translated(t: TFunction, section: string, value: string | undefined): string {
  if (value === undefined) return "";
  return known(t, section, value) ? t(`${section}.${value}`) : value;
}

/** Why a line is not imported, or a note about it. */
export function lineText(ctx: TextContext, reason: ImportReason): string {
  const { t } = ctx;
  const p = reason.params;
  if (!known(t, "importLines", reason.code)) return t("importCodes.unknown", { code: reason.code });
  const values: Record<string, string> = { ...p };
  switch (reason.code) {
    case "CELL_PROBLEM":
      values.problem = translated(t, "importCellProblems", p.problem);
      break;
    case "FIELD_INVALID":
      values.problem = translated(t, "importFieldProblems", p.problem);
      break;
    case "FIELD_RULE":
      if (ctx.format === "directa-xlsx") values.field = translated(t, "importFields", p.field);
      values.rule = p.rule ? issueText(t, p.rule) : t("importCodes.fieldRuleGeneric");
      break;
    case "ALREADY_PRESENT":
      values.match = translated(t, "importMatches", p.match);
      break;
    case "UNCONFIRMED_MOVEMENT_TYPE":
      values.mapped_as = translated(t, "importMappings", p.mapped_as);
      break;
    case "WITHHOLDING_PAIRED":
      values.amount = formatMoney(p.amount, ctx.currency, ctx.locale);
      break;
    case "DUPLICATE_ID":
      values.lines = p.lines.split(",").join(", ");
      break;
  }
  return t(`importLines.${reason.code}`, values);
}

/** A notice about the file or a target account. */
export function noticeText(ctx: TextContext, reason: ImportReason): string {
  const { t } = ctx;
  const p = reason.params;
  if (!known(t, "importNotices", reason.code)) {
    return t("importCodes.unknown", { code: reason.code });
  }
  const values: Record<string, string> = { ...p };
  if (p.account !== undefined) values.account = ctx.accountName(p.account);
  if (p.asset !== undefined) values.asset = ctx.assetName(p.asset);
  if (p.field !== undefined) values.field = translated(t, "importAssetFields", p.field);
  if (p.first_date) values.first_date = formatDay(p.first_date, ctx.locale);
  if (p.last_date) values.last_date = formatDay(p.last_date, ctx.locale);
  return t(`importNotices.${reason.code}`, values);
}

/** What must be solved before confirming. */
export function blockingText(ctx: TextContext, reason: ImportReason): string {
  const { t } = ctx;
  const p = reason.params;
  if (!known(t, "importBlocking", reason.code)) {
    return t("importCodes.unknown", { code: reason.code });
  }
  const values: Record<string, string> = { ...p };
  if (p.account !== undefined) values.account = ctx.accountName(p.account);
  if (p.asset !== undefined) values.asset = ctx.assetName(p.asset);
  if (reason.code === "ASSET_INVALID") values.rule = issueText(t, p.rule ?? "");
  if (reason.code === "QUANTITY_CONFLICT") {
    const where = p.line
      ? rowsText(t, ctx.rowsOf(p.line))
      : t("importCodes.existingTransaction");
    const conflict = known(t, "importConflicts", p.reason ?? "") ? p.reason : "NEGATIVE_QUANTITY";
    values.reason = t(`importConflicts.${conflict}`, {
      quantity: p.quantity ? formatQuantity(p.quantity, ctx.locale) : "",
      where,
    });
  }
  return t(`importBlocking.${reason.code}`, values);
}
