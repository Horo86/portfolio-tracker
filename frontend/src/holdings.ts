import { useQuery } from "@tanstack/react-query";

import { ACCOUNTS_KEY } from "./accounts";
import { api } from "./api/client";
import type {
  Account,
  AccountPosition,
  Holding,
  PortfolioPositions,
  StaleFx,
} from "./api/types";

/** A holding row in one shape, whether it comes from one account or from all (D-143). */
export type HoldingRow = Holding & { account_ids: string[]; partial: boolean; stale_fx: StaleFx[] };

export type HoldingsView = {
  currency: string; // of every amount: the account's, or the reporting currency
  partial: boolean; // something could not be valued: totals leave it out (D-64, D-73)
  unavailable_currencies: string[]; // accounts left out for a missing rate (all accounts only)
  stale_fx: StaleFx[];
  rows: HoldingRow[]; // sorted by name, closed positions included
};

const sameFx = (a: StaleFx, b: StaleFx) =>
  a.from_currency === b.from_currency &&
  a.to_currency === b.to_currency &&
  a.rate_date === b.rate_date;

export function fromAccount(account: Account, positions: AccountPosition[]): HoldingsView {
  const staleFx: StaleFx[] = [];
  for (const { fx_stale: notice } of positions) {
    if (notice && !staleFx.some((known) => sameFx(known, notice))) staleFx.push(notice);
  }
  return {
    currency: account.currency,
    partial: positions.some((p) => p.value_unavailable),
    unavailable_currencies: [],
    stale_fx: staleFx,
    rows: positions.map(({ fx_stale, ...row }) => ({
      ...row,
      account_ids: [account.id],
      partial: false,
      stale_fx: fx_stale ? [fx_stale] : [],
    })),
  };
}

export function fromPortfolio({ positions, ...rest }: PortfolioPositions): HoldingsView {
  return {
    currency: rest.currency,
    partial: rest.partial,
    unavailable_currencies: rest.unavailable_currencies,
    stale_fx: rest.stale_fx,
    rows: positions,
  };
}

/** A bond's average cost is a % of nominal only in its price currency: in another currency
 * the exchange rate is mixed into it, so it is not shown (D-143, D-66). */
export const mixedBondCost = (
  asset: { asset_type: Holding["asset_type"]; quote_currency: string },
  currency: string,
) => asset.asset_type === "BOND" && asset.quote_currency !== currency;

/** Under the accounts' key, so whatever refreshes the account figures (a transaction change,
 * the reporting currency, a deleted account) refreshes the holdings too. */
export const holdingsKey = (accountId: string | null) =>
  [...ACCOUNTS_KEY, accountId ?? "all", "holdings"] as const;

/** The holdings of the account in view, or of all accounts; closed ones included, so the
 * "show closed positions" switch needs no new request and the detail reads the same data. */
export function useHoldings(account: Account | null, enabled = true) {
  return useQuery({
    queryKey: holdingsKey(account?.id ?? null),
    enabled,
    queryFn: async ({ signal }): Promise<HoldingsView> => {
      if (account) {
        const path = `/accounts/${account.id}/positions?include_closed=true`;
        return fromAccount(account, await api<AccountPosition[]>("GET", path, undefined, signal));
      }
      const path = "/portfolio/positions?include_closed=true";
      return fromPortfolio(await api<PortfolioPositions>("GET", path, undefined, signal));
    },
  });
}
