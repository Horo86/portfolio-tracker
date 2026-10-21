import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { useSearchParams } from "react-router";

import { api } from "./api/client";
import type { Account, AccountSummary } from "./api/types";

export const ACCOUNTS_KEY = ["accounts"] as const;

export function useAccounts() {
  return useQuery({
    queryKey: ACCOUNTS_KEY,
    queryFn: ({ signal }) => api<Account[]>("GET", "/accounts", undefined, signal),
  });
}

export function useAccountSummary(id: string) {
  return useQuery({
    queryKey: [...ACCOUNTS_KEY, id, "summary"],
    queryFn: ({ signal }) =>
      api<AccountSummary>("GET", `/accounts/${id}/summary`, undefined, signal),
  });
}

const PARAM = "account";

/**
 * The account in view (sidebar "Conto in vista"), kept in the URL as `?account=<id>` (Q-6):
 * it survives a reload and follows the back button. An id that is not one of the user's
 * accounts (deleted meanwhile, mistyped) means all accounts.
 */
export function useAccountInView() {
  const [params, setParams] = useSearchParams();
  const accounts = useAccounts();
  const id = params.get(PARAM);
  const account = (id && accounts.data?.find((a) => a.id === id)) || null;
  // Until the list arrives the id is kept as it is, so links do not drop it.
  const kept = accounts.data ? account?.id : id;

  const setAccount = useCallback(
    (next: string | null) =>
      setParams((current) => {
        const updated = new URLSearchParams(current);
        if (next) updated.set(PARAM, next);
        else updated.delete(PARAM);
        updated.delete("page"); // a list of another account starts from its first page
        return updated;
      }),
    [setParams],
  );

  return {
    account,
    accounts: accounts.data ?? [],
    // An id in the URL that cannot be checked yet: data for "all accounts" would be wrong.
    pending: id !== null && accounts.data === undefined,
    // The query string that carries the account in view to another page.
    search: kept ? `?${new URLSearchParams({ [PARAM]: kept })}` : "",
    setAccount,
  };
}

/** The address of a page with the given account in view. */
export function withAccount(pathname: string, id: string): string {
  return `${pathname}?${new URLSearchParams({ [PARAM]: id })}`;
}
