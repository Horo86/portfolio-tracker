import { keepPreviousData, type QueryClient, useQuery } from "@tanstack/react-query";

import { ACCOUNTS_KEY } from "./accounts";
import { api, ApiError } from "./api/client";
import type {
  Asset,
  BatchIn,
  BatchPreview,
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

export function useAssets(enabled = true) {
  return useQuery({
    queryKey: ASSETS_KEY,
    queryFn: ({ signal }) => api<Asset[]>("GET", "/assets", undefined, signal),
    enabled,
  });
}

// Not under TRANSACTIONS_KEY: a save must not ask again for the input it just saved. A dry
// run is never reused from the cache for long (gcTime); it is fresh until a change makes it
// out of date (afterChange). Save always asks afresh with its own request (previewNow).
const PREVIEW_KEY = ["transaction-preview"] as const;
const ONE = "/transactions/preview";
const BATCH = "/transactions/batch/preview";

/**
 * A live dry run (D-139): of the input, sent once it has not changed for 300 ms. A request
 * whose input has changed meanwhile is aborted, and an answer that arrives late lands under
 * its own input, never under the current one. `body` null: not complete enough to ask.
 */
function useDryRun<T>(path: string, body: unknown) {
  const typed = body ? JSON.stringify(body) : null;
  const sent = useDebounced(typed, PREVIEW_DELAY_MS);
  const query = useQuery({
    queryKey: [...PREVIEW_KEY, path, sent ?? ""],
    queryFn: ({ signal }) => api<T>("POST", path, JSON.parse(sent as string), signal),
    enabled: sent !== null,
    placeholderData: keepPreviousData,
    retry: false,
    gcTime: 30_000,
    staleTime: Infinity,
  });
  // The answer shown is the one for what is in the form now; one from before the last change
  // (out of date) is not, while it is asked again.
  const current =
    typed !== null &&
    typed === sent &&
    !query.isPlaceholderData &&
    !(query.isStale && query.isFetching);
  return { ...query, current };
}

/** The live summary of the transaction form: a dry run of its one change. */
export function usePreview(body: PreviewIn | null) {
  return useDryRun<Preview>(ONE, body);
}

/** The live totals of "add holdings I already own": a dry run of its rows (D-147). */
export function useBatchPreview(body: BatchIn | null) {
  return useDryRun<BatchPreview>(BATCH, body);
}

/** A fresh dry run of the input, for Save: the form saves exactly its completed
 * transaction (D-139). Its own request, outside the cache: the live summary leaving the same
 * input (the user typing) cannot cancel it. */
export function previewNow(body: PreviewIn): Promise<Preview> {
  return api<Preview>("POST", ONE, body);
}

/** The same for the rows of a batch: Save sends exactly the rows it completed. */
export function batchPreviewNow(body: BatchIn): Promise<BatchPreview> {
  return api<BatchPreview>("POST", BATCH, body);
}

/** After a change: lists, previews, account figures and counts are out of date. A dry run
 * from before it is never shown again as current: it is asked again when next shown. */
export function afterChange(client: QueryClient): Promise<unknown> {
  return Promise.all([
    client.invalidateQueries({ queryKey: TRANSACTIONS_KEY }),
    client.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
    client.invalidateQueries({ queryKey: PREVIEW_KEY, refetchType: "none" }),
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

/** The field issues of a 422 VALIDATION_FAILED about the rows of a batch, by row (`index`). */
export function rowIssuesOf(error: unknown): Map<number, FieldIssue[]> {
  const rows = new Map<number, FieldIssue[]>();
  if (!(error instanceof ApiError) || error.code !== "VALIDATION_FAILED") return rows;
  const entries = error.extra.transactions;
  if (!Array.isArray(entries)) return rows;
  for (const entry of entries as TransactionIssues[]) {
    if (typeof entry.index === "number") rows.set(entry.index, entry.issues ?? []);
  }
  return rows;
}
