import type { TFunction } from "i18next";

import { ApiError } from "./api/client";

// Every error code the screens of this version can receive, each with a text in it.json and
// en.json (checked by a test). NETWORK, SERVER and HTTP_ERROR come from the client itself.
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
  "FORBIDDEN",
  "CONFLICT",
  "BAD_REQUEST",
  "METHOD_NOT_ALLOWED",
  "HTTP_ERROR",
  "NETWORK",
  "SERVER",
] as const;

const KNOWN: ReadonlySet<string> = new Set(KNOWN_ERROR_CODES);

/** The translated text for an error; never the server's `detail`. */
export function errorText(t: TFunction, error: unknown): string {
  if (!(error instanceof ApiError)) return t("errors.UNKNOWN", { code: "?" });
  const text = KNOWN.has(error.code)
    ? t(`errors.${error.code}`)
    : t("errors.UNKNOWN", { code: error.code });
  if (error.code !== "TOO_MANY_ATTEMPTS") return text;
  const wait =
    error.retryAfter === null
      ? t("errors.retryLater")
      : t("errors.retryIn", { count: Math.max(1, Math.ceil(error.retryAfter / 60)) });
  return `${text} ${wait}`;
}
