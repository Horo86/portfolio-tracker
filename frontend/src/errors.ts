import type { TFunction } from "i18next";

import { ApiError } from "./api/client";

// Every error code the screens of this version can receive, each with a text in it.json and
// en.json (checked by a test). NETWORK, SERVER and HTTP_ERROR come from the client itself.
// A code with a text that is missing here is still shown with its text (errorText).
export const KNOWN_ERROR_CODES = [
  "NOT_AUTHENTICATED",
  "INVALID_CREDENTIALS",
  "CSRF_CHECK_FAILED",
  "PASSWORD_CHANGE_REQUIRED",
  "NOT_FOUND",
  "CURRENCY_LOCKED",
  "SETUP_ALREADY_DONE",
  "CONFIRMATION_MISMATCH",
  "CURRENT_PASSWORD_WRONG",
  "PASSWORD_TOO_SHORT",
  "PASSWORD_TOO_LONG",
  "PASSWORD_UNCHANGED",
  "PASSWORD_CHANGED_MEANWHILE",
  "TOO_MANY_ATTEMPTS",
  "REQUEST_INVALID",
  "REQUEST_TOO_LARGE",
  "VALIDATION_FAILED",
  "NOT_IMPLEMENTED",
  "QUANTITY_CONFLICT",
  "CURRENCY_MISMATCH",
  "ISIN_ALREADY_USED",
  "ASSET_IN_USE",
  "ASSET_READ_ONLY",
  "ASSET_HAS_PRICES",
  "IMPORT_TOO_LARGE",
  "IMPORT_FILE_INVALID",
  "IMPORT_FORMAT_UNKNOWN",
  "IMPORT_FORMAT_VERSION",
  "IMPORT_ACCOUNT_REQUIRED",
  "IMPORT_ACCOUNT_CURRENCY",
  "IMPORT_CHOICE_INVALID",
  "IMPORT_SELECTION_INVALID",
  "IMPORT_PREVIEW_STALE",
  "IMPORT_NOT_READY",
  "IMPORT_BUSY",
  "MARKET_DATA_OFF",
  "MARKET_DATA_BUSY",
  "MARKET_DATA_UNAVAILABLE",
  "FORBIDDEN",
  "CONFLICT",
  "BAD_REQUEST",
  "METHOD_NOT_ALLOWED",
  "HTTP_ERROR",
  "NETWORK",
  "SERVER",
] as const;

const CODE = /^[A-Z][A-Z0-9_]*$/; // the other keys under `errors` are not codes

/** The translated text for an error; never the server's `detail`. A code without a text is
 * named. */
export function errorText(t: TFunction, error: unknown): string {
  if (!(error instanceof ApiError)) return t("errors.UNKNOWN", { code: "?" });
  const own = CODE.test(error.code) ? t(`errors.${error.code}`, { defaultValue: "" }) : "";
  const text = own || t("errors.UNKNOWN", { code: error.code });
  // The price source tells how many seconds it stays busy (D-128).
  if (error.code === "MARKET_DATA_BUSY") {
    const wait =
      error.retryAfter === null
        ? t("errors.retryLater")
        : t("errors.retryInSeconds", { count: Math.max(1, error.retryAfter) });
    return `${text} ${wait}`;
  }
  if (error.code !== "TOO_MANY_ATTEMPTS") return text;
  const wait =
    error.retryAfter === null
      ? t("errors.retryLater")
      : t("errors.retryIn", { count: Math.max(1, Math.ceil(error.retryAfter / 60)) });
  return `${text} ${wait}`;
}

// The field issue codes of VALIDATION_FAILED (engine/validation.py, too_many_digits from the
// preview, D-139, future_date from a manual price, D-149), each with a text under `issues`
// (checked against the backend by a test).
export const KNOWN_ISSUE_CODES = [
  "required",
  "not_allowed",
  "must_be_positive",
  "must_not_be_negative",
  "too_many_decimals",
  "too_many_digits",
  "invariant",
  "bond_only",
  "must_be_bond",
  "mismatch",
  "invalid_asset",
  "must_match_asset_type",
  "future_date",
  "after_date_to",
] as const;

const KNOWN_ISSUES: ReadonlySet<string> = new Set(KNOWN_ISSUE_CODES);

/** The text of a field issue; an unknown code is named rather than hidden. */
export function issueText(t: TFunction, code: string): string {
  return KNOWN_ISSUES.has(code) ? t(`issues.${code}`) : t("issues.unknown", { code });
}
