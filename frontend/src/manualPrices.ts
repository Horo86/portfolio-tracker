// A security's manual prices (screen 12, D-149): the user's own only, never provider closes
// (D-116), one per day; the route returns them all, newest first.

import { type QueryClient, useQuery } from "@tanstack/react-query";

import { ACCOUNTS_KEY } from "./accounts";
import { api } from "./api/client";
import type { ManualPrice } from "./api/types";
import { ASSETS_KEY } from "./transactions";

export const SHOWN_PRICES = 20; // D-149: the latest ones, then "show all"

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Under the assets' key: whatever reloads the user's securities reloads their prices too. */
export const manualPricesKey = (assetId: string) => [...ASSETS_KEY, assetId, "prices"] as const;

export function useManualPrices(assetId: string, enabled = true) {
  return useQuery({
    queryKey: manualPricesKey(assetId),
    enabled,
    queryFn: ({ signal }) =>
      api<ManualPrice[]>("GET", `/assets/${assetId}/prices`, undefined, signal),
  });
}

/** After a price is saved or deleted: the list, and every figure valued with it (holdings
 * and account figures live under the accounts' key). */
export function afterPriceChange(client: QueryClient, assetId: string): Promise<unknown> {
  return Promise.all([
    client.invalidateQueries({ queryKey: manualPricesKey(assetId) }),
    client.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  ]);
}

/** What stops a day in the page: none, or one after the browser's today. The server allows
 * tomorrow by its own date (D-149), for a user ahead of its time zone. */
export function dayProblem(day: string, today: string): "required" | "future" | null {
  if (!DAY.test(day)) return "required";
  return day > today ? "future" : null;
}
