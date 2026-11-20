import type { PriceRole } from "./api/types";

// The price-sources page (D-196): pure helpers, tested alone.

/** The roles a change may not leave without an enabled engine unless the administrator
 * confirms (D-188 (6), read by D-196): prices, rates and the classification of new
 * securities. Bonds have no engine by default (manual prices, D-184). */
export const GUARDED_ROLES: readonly PriceRole[] = ["ETF_STOCK", "FX", "CLASSIFY"];

/** The guarded roles that have an enabled engine in `before` and none in `after`. */
export function leftEmpty(
  orders: Partial<Record<PriceRole, readonly string[]>>,
  before: ReadonlySet<string>,
  after: { orders?: Partial<Record<PriceRole, readonly string[]>>; enabled?: ReadonlySet<string> },
): PriceRole[] {
  const enabledAfter = after.enabled ?? before;
  return GUARDED_ROLES.filter((role) => {
    const was = orders[role] ?? [];
    const will = after.orders?.[role] ?? was;
    return was.some((name) => before.has(name)) && !will.some((name) => enabledAfter.has(name));
  });
}

/** `list` with the item at `index` moved by `step` (−1 up, +1 down); the same list when it
 * cannot move. */
export function moved<T>(list: readonly T[], index: number, step: -1 | 1): T[] {
  const target = index + step;
  if (index < 0 || index >= list.length || target < 0 || target >= list.length) return [...list];
  const next = [...list];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export const MAX_EXCHANGES = 20; // PUT exchanges: at most 20 (D-188 (1))
const MAX_EXCHANGE_LENGTH = 64; // routing.check_exchanges: [^\s,][^,]{0,63}

export type ExchangeProblem = "required" | "comma" | "tooLong" | "repeat" | "tooMany";

/** The API's own checks of a new preferred exchange (D-188 (1)), made before sending so the
 * problem is said in the user's language: stripped, not empty, no comma, at most 64
 * characters, not already listed ignoring case, at most 20 in all. */
export function exchangeProblem(value: string, list: readonly string[]): ExchangeProblem | null {
  const text = value.trim();
  if (text === "") return "required";
  if (text.includes(",")) return "comma";
  if ([...text].length > MAX_EXCHANGE_LENGTH) return "tooLong";
  const folded = text.toLowerCase();
  if (list.some((item) => item.toLowerCase() === folded)) return "repeat";
  if (list.length >= MAX_EXCHANGES) return "tooMany";
  return null;
}
