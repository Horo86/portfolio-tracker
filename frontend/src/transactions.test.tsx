import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ApiError } from "./api/client";
import type { BatchIn, BatchPreview, Preview, PreviewIn } from "./api/types";
import { type Call, stubFetch } from "./test/fetchStub";
import {
  afterChange,
  batchPreviewNow,
  conflictOf,
  issuesOf,
  previewNow,
  rowIssuesOf,
  useAssets,
  useBatchPreview,
  usePreview,
} from "./transactions";

// Invented ids and amounts only.
const ACCOUNT = "00000000-0000-4000-8000-0000000000a1";
const ASSET = "00000000-0000-4000-8000-0000000000b1";
const PREVIEW = "POST /api/v1/transactions/preview";
const echo = { [PREVIEW]: (call: Call) => ({ body: answer(call.body as PreviewIn) }) };

const buy = (quantity: string): PreviewIn => ({
  transaction: {
    account_id: ACCOUNT,
    type: "BUY",
    trade_date: "2026-10-12",
    asset_id: ASSET,
    quantity,
    unit_price: "101.80",
  },
});

// The quantity comes back as the total, so a test can tell which input an answer is for.
const answer = ({ transaction }: PreviewIn): Preview => ({
  transaction: { ...transaction!, gross_amount: "x", total_amount: transaction!.quantity },
  derived: ["gross_amount", "total_amount"],
  account_id: ACCOUNT,
  currency: "EUR",
  cash: { before: "0", change: "0", after: "0" },
  position: null,
  warnings: [],
});

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

describe("usePreview", () => {
  it("asks once the input stops changing, with the latest input", async () => {
    const { calls } = stubFetch(echo);
    const { wrapper } = setup();
    const { result, rerender } = renderHook((body: PreviewIn | null) => usePreview(body), {
      wrapper,
      initialProps: null as PreviewIn | null,
    });
    rerender(buy("1"));
    rerender(buy("15"));
    rerender(buy("150"));
    expect(result.current.current).toBe(false);
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(calls.map((c) => c.body)).toEqual([buy("150")]);
    expect(result.current.data?.transaction?.total_amount).toBe("150");
  });

  it("aborts a request whose input changed and never shows a late answer", async () => {
    let releaseFirst: () => void = () => {};
    const { calls } = stubFetch({
      [PREVIEW]: (call) => {
        const body = call.body as PreviewIn;
        if (body.transaction?.quantity !== "1") return { body: answer(body) };
        return new Promise((resolve) => {
          releaseFirst = () => resolve({ body: answer(body) });
        });
      },
    });
    const { wrapper } = setup();
    const { result, rerender } = renderHook((body: PreviewIn | null) => usePreview(body), {
      wrapper,
      initialProps: buy("1"),
    });
    await waitFor(() => expect(calls).toHaveLength(1));
    rerender(buy("2"));
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(calls[0].signal?.aborted).toBe(true);
    releaseFirst();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(result.current.data?.transaction?.quantity).toBe("2");
  });

  it("asks nothing while the input is not complete", async () => {
    stubFetch({});
    const { wrapper } = setup();
    const { result } = renderHook(() => usePreview(null), { wrapper });
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(result.current.current).toBe(false);
    expect(result.current.data).toBeUndefined();
  });
});

const previews = (client: QueryClient) =>
  client.getQueryCache().findAll({ queryKey: ["transaction-preview"] });

describe("previewNow", () => {
  it("always asks again, with its own request outside the live summary's cache", async () => {
    const { calls } = stubFetch(echo);
    const { client, wrapper } = setup();
    const live = renderHook(() => usePreview(buy("3")), { wrapper });
    await waitFor(() => expect(live.result.current.current).toBe(true));
    const second = await previewNow(buy("3"));
    // Save asked the server itself: the live summary's entry was neither used nor shared.
    expect(calls).toHaveLength(2);
    expect(second.transaction?.total_amount).toBe("3");
    expect(previews(client)).toHaveLength(1);
  });
});

describe("afterChange", () => {
  it("an answer from before a change is never shown as current: it is asked again", async () => {
    const { calls } = stubFetch(echo);
    const { client, wrapper } = setup();
    const first = renderHook(() => usePreview(buy("3")), { wrapper });
    await waitFor(() => expect(first.result.current.current).toBe(true));
    first.unmount();
    await afterChange(client);
    expect(calls).toHaveLength(1);

    // The same input again (e.g. after "Save and add another"): the cached answer is out of
    // date, so it is not current until the server has answered again.
    const again = renderHook(() => usePreview(buy("3")), { wrapper });
    expect(again.result.current.data).toBeDefined();
    expect(again.result.current.current).toBe(false);
    await waitFor(() => expect(again.result.current.current).toBe(true));
    expect(calls).toHaveLength(2);
  });

  it("asks for the securities again: whether a transaction uses one may have changed (D-150)", async () => {
    const { calls } = stubFetch({ "GET /api/v1/assets": { body: [] } });
    const { client, wrapper } = setup();
    const assets = renderHook(() => useAssets(), { wrapper });
    await waitFor(() => expect(assets.result.current.isSuccess).toBe(true));
    await afterChange(client);
    expect(calls.filter((c) => c.path === "/api/v1/assets")).toHaveLength(2);
  });
});

const BATCH = "POST /api/v1/transactions/batch/preview";

const rows = (...quantities: string[]): BatchIn => ({
  transactions: quantities.map((quantity) => ({ ...buy(quantity).transaction!, affects_cash: false })),
});

// Each row's quantity comes back as its total, so a test can tell which input it is for.
const batchAnswer = ({ transactions }: BatchIn): BatchPreview => ({
  transactions: transactions.map((tx) => ({ ...tx, gross_amount: "x", total_amount: tx.quantity })),
  derived: transactions.map(() => ["gross_amount", "total_amount"]),
  warnings: [],
});
const batchEcho = { [BATCH]: (call: Call) => ({ body: batchAnswer(call.body as BatchIn) }) };

describe("useBatchPreview", () => {
  it("asks once the rows stop changing, with the latest rows", async () => {
    const { calls } = stubFetch(batchEcho);
    const { wrapper } = setup();
    const { result, rerender } = renderHook((body: BatchIn | null) => useBatchPreview(body), {
      wrapper,
      initialProps: null as BatchIn | null,
    });
    rerender(rows("1"));
    rerender(rows("1", "2"));
    expect(result.current.current).toBe(false);
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(calls.map((c) => c.body)).toEqual([rows("1", "2")]);
    expect(result.current.data?.transactions.map((tx) => tx.total_amount)).toEqual(["1", "2"]);
  });

  it("aborts a request whose rows changed and never shows a late answer", async () => {
    let releaseFirst: () => void = () => {};
    const { calls } = stubFetch({
      [BATCH]: (call) => {
        const body = call.body as BatchIn;
        if (body.transactions.length > 1) return { body: batchAnswer(body) };
        return new Promise((resolve) => {
          releaseFirst = () => resolve({ body: batchAnswer(body) });
        });
      },
    });
    const { wrapper } = setup();
    const { result, rerender } = renderHook((body: BatchIn | null) => useBatchPreview(body), {
      wrapper,
      initialProps: rows("1"),
    });
    await waitFor(() => expect(calls).toHaveLength(1));
    rerender(rows("1", "5"));
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(calls[0].signal?.aborted).toBe(true);
    releaseFirst();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(result.current.data?.transactions).toHaveLength(2);
  });

  it("is out of date after a change, like the preview of one transaction", async () => {
    const { calls } = stubFetch(batchEcho);
    const { client, wrapper } = setup();
    const first = renderHook(() => useBatchPreview(rows("3")), { wrapper });
    await waitFor(() => expect(first.result.current.current).toBe(true));
    first.unmount();
    await afterChange(client);
    const again = renderHook(() => useBatchPreview(rows("3")), { wrapper });
    expect(again.result.current.current).toBe(false);
    await waitFor(() => expect(again.result.current.current).toBe(true));
    expect(calls).toHaveLength(2);
  });
});

describe("batchPreviewNow", () => {
  it("always asks again, outside the live preview's cache", async () => {
    const { calls } = stubFetch(batchEcho);
    const { client, wrapper } = setup();
    const live = renderHook(() => useBatchPreview(rows("3")), { wrapper });
    await waitFor(() => expect(live.result.current.current).toBe(true));
    const fresh = await batchPreviewNow(rows("3"));
    expect(calls).toHaveLength(2);
    expect(fresh.transactions[0].total_amount).toBe("3");
    expect(previews(client)).toHaveLength(1);
  });
});

describe("error details", () => {
  it("reads the field issues of a 422 row by row", () => {
    const first = [{ field: "quantity", code: "must_be_positive" }];
    const third = [{ field: "unit_price", code: "required" }];
    const error = new ApiError(422, "VALIDATION_FAILED", {
      transactions: [
        { index: 0, issues: first },
        { index: 2, issues: third },
        { transaction_id: ASSET, issues: third },
      ],
    });
    expect(rowIssuesOf(error)).toEqual(
      new Map([
        [0, first],
        [2, third],
      ]),
    );
    expect(rowIssuesOf(new ApiError(409, "QUANTITY_CONFLICT"))).toEqual(new Map());
  });

  it("reads the conflict of a 409 and the field issues of a 422", () => {
    const conflict = {
      reason: "NEGATIVE_QUANTITY",
      index: 0,
      account_id: ACCOUNT,
      asset_id: ASSET,
      quantity: "-10",
      held: "50",
    };
    expect(conflictOf(new ApiError(409, "QUANTITY_CONFLICT", { conflict }))).toEqual(conflict);
    expect(conflictOf(new ApiError(409, "CONFLICT"))).toBeNull();
    const issues = [{ field: "gross_amount", code: "too_many_digits" }];
    const error = new ApiError(422, "VALIDATION_FAILED", { transactions: [{ index: 0, issues }] });
    expect(issuesOf(error)).toEqual(issues);
    expect(issuesOf(new ApiError(422, "REQUEST_INVALID"))).toEqual([]);
  });
});
