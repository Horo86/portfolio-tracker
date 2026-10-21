import { keepPreviousData, type QueryClient, useQuery } from "@tanstack/react-query";

import { ACCOUNTS_KEY } from "./accounts";
import { api, ApiError } from "./api/client";
import type {
  Asset,
  FieldIssue,
  Preview,
  PreviewIn,
  QuantityConflict,
  TransactionIssues,
  Transaction,
  TransactionPage,
  TransactionType,
} from "./api/types";
import { useDebounced } from "./useDebounced";

export const TRANSACTIONS_KEY = ["transactions"] as const;
export const ASSETS_KEY = ["assets"] as const;

export const PAGE_SIZE = 50; // D-141

export const TRANSACTION_TYPES: readonly TransactionType[] = [
  "BUY",
  "SELL",
  "DIVIDEND",
  "COUPON",
  "DEPOSIT",
  "WITHDRAWAL",
  "STOCK_SPLIT",
];
export const PREVIEW_DELAY_MS = 300;

export type TransactionFilter = {
  account?: string;
  asset?: string;
  type?: string;
  from?: string;
  to?: string;
  page: number; // from 0
};

export function useTransactions(filter: TransactionFilter, enabled = true) {
  const params = new URLSearchParams();
  if (filter.account) params.set("account_id", filter.account);
  if (filter.asset) params.set("asset_id", filter.asset);
  if (filter.type) params.set("type", filter.type);
  if (filter.from) params.set("date_from", filter.from);
  if (filter.to) params.set("date_to", filter.to);
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(filter.page * PAGE_SIZE));
  const query = params.toString();
  return useQuery({
    queryKey: [...TRANSACTIONS_KEY, "list", query],
    queryFn: ({ signal }) =>
      api<TransactionPage>("GET", `/transactions?${query}`, undefined, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useTransaction(id: string | undefined) {
  return useQuery({
    queryKey: [...TRANSACTIONS_KEY, "one", id],
    queryFn: ({ signal }) => api<Transaction>("GET", `/transactions/${id}`, undefined, signal),
    enabled: id !== undefined,
  });
}

export function useAssets() {
  return useQuery({
    queryKey: ASSETS_KEY,
    queryFn: ({ signal }) => api<Asset[]>("GET", "/assets", undefined, signal),
  });
}

// Not under TRANSACTIONS_KEY: a save must not ask again for the input it just saved. A dry
// run is never reused from the cache for long (gcTime) and Save always asks afresh.
const previewKey = (body: string) => ["transaction-preview", body] as const;

const fetchPreview = (body: string, signal?: AbortSignal) =>
  api<Preview>("POST", "/transactions/preview", JSON.parse(body), signal);

/**
 * The live summary (D-139): a dry run of the form's current input, sent once the input has
 * not changed for 300 ms. A request whose input has changed meanwhile is aborted, and an
 * answer that arrives late lands under its own input, never under the current one.
 * `body` null: the input is not complete enough to ask.
 */
export function usePreview(body: PreviewIn | null) {
  const typed = body ? JSON.stringify(body) : null;
  const sent = useDebounced(typed, PREVIEW_DELAY_MS);
  const query = useQuery({
    queryKey: previewKey(sent ?? ""),
    queryFn: ({ signal }) => fetchPreview(sent as string, signal),
    enabled: sent !== null,
    placeholderData: keepPreviousData,
    retry: false,
    gcTime: 30_000,
  });
  // The answer shown is the one for what is in the form now.
  const current = typed !== null && typed === sent && !query.isPlaceholderData;
  return { ...query, current };
}

/** A fresh dry run of the input, for Save: the form saves exactly its completed
 * transaction (D-139). */
export function previewNow(client: QueryClient, body: PreviewIn): Promise<Preview> {
  const key = JSON.stringify(body);
  return client.fetchQuery({
    queryKey: previewKey(key),
    queryFn: ({ signal }) => fetchPreview(key, signal),
    staleTime: 0,
    retry: false,
  });
}

/** After a change: lists, previews, account figures and counts are out of date. */
export function afterChange(client: QueryClient): Promise<unknown> {
  return Promise.all([
    client.invalidateQueries({ queryKey: TRANSACTIONS_KEY }),
    client.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  ]);
}

/** The conflict of a 409 QUANTITY_CONFLICT, or null. */
export function conflictOf(error: unknown): QuantityConflict | null {
  if (!(error instanceof ApiError) || error.code !== "QUANTITY_CONFLICT") return null;
  const conflict = error.extra.conflict;
  return conflict && typeof conflict === "object" ? (conflict as QuantityConflict) : null;
}

/** The field issues of a 422 VALIDATION_FAILED about the one transaction of a form. */
export function issuesOf(error: unknown): FieldIssue[] {
  if (!(error instanceof ApiError) || error.code !== "VALIDATION_FAILED") return [];
  const entries = error.extra.transactions;
  if (!Array.isArray(entries)) return [];
  return (entries as TransactionIssues[]).flatMap((entry) => entry.issues ?? []);
}
