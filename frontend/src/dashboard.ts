import type { Me, StaleFx } from "./api/types";
import { compareDecimal } from "./format/decimal";
import type { HoldingRow } from "./holdings";
import { type StatusCode, staleKey, statusCode, statusTone } from "./priceStatus";

// What the dashboard (01) chooses from the positions and the summary (D-153); the figures
// themselves come from the server, the browser only picks, orders and counts rows.

export type PriceNotice = {
  row: HoldingRow;
  code: StatusCode | null; // a status that needs the user to act, or null
  stale: ReturnType<typeof staleKey>;
};

const BANNER_LINES = 6;

/** The banner's lines (D-153 (6), Q-15): one per open security whose price needs the user
 * (stale, failing, no longer listed, mismatch, no source), up to 6, else 5 and how many more;
 * `PENDING` only counted for a quiet note; market data off said once (D-145). */
export function priceNotices(rows: HoldingRow[], marketData: Me["market_data"]) {
  const notices: PriceNotice[] = [];
  let pending = 0;
  let off = false;
  for (const row of rows.filter((r) => r.open)) {
    const code = statusCode(row, marketData);
    if (code === "PENDING") pending += 1;
    if (code === "OFF") off = true;
    const acts = code !== null && statusTone(code) === "warning" ? code : null;
    const stale = staleKey(row, code);
    if (acts || stale) notices.push({ row, code: acts, stale });
  }
  const cut = notices.length > BANNER_LINES ? BANNER_LINES - 1 : notices.length;
  return { shown: notices.slice(0, cut), more: notices.length - cut, pending, off };
}

/** Each stale rate once per pair of currencies: a pair and its inverse are one notice, with
 * the older date (D-153 (6)). */
export function uniquePairs(notices: StaleFx[]): StaleFx[] {
  const pairs: StaleFx[] = [];
  for (const notice of notices) {
    const i = pairs.findIndex(
      (p) =>
        (p.from_currency === notice.from_currency && p.to_currency === notice.to_currency) ||
        (p.from_currency === notice.to_currency && p.to_currency === notice.from_currency),
    );
    if (i < 0) pairs.push(notice);
    else if (notice.rate_date < pairs[i].rate_date) {
      pairs[i] = { ...pairs[i], rate_date: notice.rate_date };
    }
  }
  return pairs;
}

const RANKED_EACH_SIDE = 3;

/** Best and worst (D-153 (5), §13): open positions with a real price only, by unrealized %,
 * best first; past 6 the 3 best and the 3 worst. `atCost` counts those valued at cost. */
export function bestWorst(rows: HoldingRow[]) {
  const open = rows.filter((r) => r.open);
  const ranked = open
    .filter(
      (r) =>
        r.unrealized_pct !== null && !r.estimated_at_cost && !r.value_unavailable && !r.partial,
    )
    .sort(
      (a, b) =>
        compareDecimal(b.unrealized_pct as string, a.unrealized_pct as string) ||
        a.name.localeCompare(b.name),
    );
  const both = 2 * RANKED_EACH_SIDE;
  return {
    ranked:
      ranked.length > both
        ? [...ranked.slice(0, RANKED_EACH_SIDE), ...ranked.slice(-RANKED_EACH_SIDE)]
        : ranked,
    atCost: open.filter((r) => r.estimated_at_cost).length,
  };
}

const LARGEST = 10;

/** The allocation by security: the 10 largest, the rest apart under "Altri (N)" (D-153 (3)).
 * The server sorts them by value. */
export function largest<T>(parts: T[]) {
  return { shown: parts.slice(0, LARGEST), rest: parts.slice(LARGEST) };
}
