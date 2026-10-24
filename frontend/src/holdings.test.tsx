import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type { Account, PortfolioPositions } from "./api/types";
import { fromAccount, fromPortfolio, useHoldings, viewNotices } from "./holdings";
import { stubFetch } from "./test/fetchStub";
import { portfolioRow, WORLD_ETF } from "./test/holdings";

const ACCOUNT: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 3,
  currency_locked: true,
};

const STALE = { from_currency: "USD", to_currency: "EUR", rate_date: "2026-10-10" };

const PORTFOLIO: PortfolioPositions = {
  currency: "EUR",
  day: "2026-10-24",
  partial: true,
  unavailable_currencies: ["CHF"],
  stale_fx: [STALE],
  positions: [portfolioRow(WORLD_ETF, { account_ids: ["a", "b"] })],
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("holdings of one account or of all accounts (D-143)", () => {
  it("gives one account's rows the shape of the aggregated ones", () => {
    const unvalued = { ...WORLD_ETF, asset_id: "x", value_unavailable: true, fx_stale: STALE };
    const view = fromAccount(ACCOUNT, [WORLD_ETF, unvalued, { ...unvalued, asset_id: "y" }]);
    expect(view.currency).toBe("EUR");
    expect(view.unavailable_currencies).toEqual([]);
    // A value is missing: the totals leave it out (D-64); each stale pair is named once.
    expect(viewNotices(view, view.rows)).toEqual({ partial: true, staleFx: [STALE] });
    expect(viewNotices(view, view.rows.slice(0, 1))).toEqual({ partial: false, staleFx: [] });
    expect(view.rows.map((r) => r.account_ids)).toEqual([[ACCOUNT.id], [ACCOUNT.id], [ACCOUNT.id]]);
    expect(view.rows[1].stale_fx).toEqual([STALE]);
    expect(view.rows[0].partial).toBe(false);
  });

  it("keeps the aggregated answer as it is", () => {
    const view = fromPortfolio(PORTFOLIO);
    expect(view).toMatchObject({ currency: "EUR", unavailable_currencies: ["CHF"] });
    expect(viewNotices(view, []).partial).toBe(true); // an account is left out (D-73)
    expect(view.rows[0].account_ids).toEqual(["a", "b"]);
  });

  it("asks once with the closed positions, for the account in view or for all", async () => {
    const { calls } = stubFetch({
      "GET /api/v1/accounts/00000000-0000-4000-8000-0000000000a1/positions?include_closed=true": {
        body: [WORLD_ETF],
      },
      "GET /api/v1/portfolio/positions?include_closed=true": { body: PORTFOLIO },
    });
    const one = renderHook(() => useHoldings(ACCOUNT), { wrapper });
    await waitFor(() => expect(one.result.current.data?.rows).toHaveLength(1));
    const all = renderHook(() => useHoldings(null), { wrapper });
    await waitFor(() => expect(all.result.current.data?.currency).toBe("EUR"));
    expect(calls.map((c) => c.path)).toEqual([
      "/api/v1/accounts/00000000-0000-4000-8000-0000000000a1/positions?include_closed=true",
      "/api/v1/portfolio/positions?include_closed=true",
    ]);
  });
});
