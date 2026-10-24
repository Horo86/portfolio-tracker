import type { Holding, Me } from "./api/types";

// Where a holding's prices stand, as the screens say it (ui README, "Alignment with M4"; D-144,
// D-145). The API's statuses are facts about the asset; the text is chosen here.

export const STATUS_CODES = [
  "OK",
  "PENDING",
  "NOT_AVAILABLE",
  "MANUAL",
  "MISMATCH",
  "MISMATCH_CURRENCY",
  "MISMATCH_UNIT",
  "MISMATCH_LEVEL",
  "FAILING",
  "NOT_LISTED",
  "OFF",
] as const;

export type StatusCode = (typeof STATUS_CODES)[number];

const SIMPLE: ReadonlySet<string> = new Set(["OK", "PENDING", "NOT_AVAILABLE", "MANUAL"]);
const MISMATCHES: ReadonlySet<string> = new Set(["CURRENCY", "UNIT", "LEVEL"]);

/** The code to show, or null for a status this version does not know (a newer server). */
export function statusCode(
  holding: Pick<Holding, "isin" | "price_status" | "price_status_detail">,
  marketData: Me["market_data"],
): StatusCode | null {
  // Market data off: an asset with an ISIN gets no automatic price whatever its status says.
  if (holding.isin && marketData === "OFF") return "OFF";
  const { price_status: status, price_status_detail: detail } = holding;
  if (SIMPLE.has(status)) return status as StatusCode;
  if (status === "MISMATCH") {
    return detail && MISMATCHES.has(detail) ? (`MISMATCH_${detail}` as StatusCode) : "MISMATCH";
  }
  if (status === "FAILING") return detail === "NOT_LISTED" ? "NOT_LISTED" : "FAILING";
  return null;
}

/** "warning": the user has something to do (the dashboard banner's list, Q-15). */
export function statusTone(code: StatusCode): "ok" | "info" | "warning" {
  if (code === "OK") return "ok";
  return code === "PENDING" || code === "MANUAL" || code === "OFF" ? "info" : "warning";
}

// Only where a provider still fetches can the next daily run bring a newer price.
const UPDATED_DAILY: ReadonlySet<StatusCode | null> = new Set(["OK", "PENDING", "FAILING"]);

/** The key of the stale-price text (older than 5 days, §12), or null when not stale. */
export function staleKey(
  holding: Pick<Holding, "price_stale" | "price_date">,
  code: StatusCode | null,
): "priceStatus.stale" | "priceStatus.staleNext" | null {
  if (!holding.price_stale || holding.price_date === null) return null;
  return UPDATED_DAILY.has(code) ? "priceStatus.staleNext" : "priceStatus.stale";
}
