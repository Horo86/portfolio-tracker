import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ApiError } from "./api/client";
import type { Preview, PreviewIn } from "./api/types";
import { stubFetch } from "./test/fetchStub";
import { conflictOf, issuesOf, previewNow, usePreview } from "./transactions";

// Invented ids and amounts only.
const ACCOUNT = "00000000-0000-4000-8000-0000000000a1";
const ASSET = "00000000-0000-4000-8000-0000000000b1";
const PREVIEW = "POST /api/v1/transactions/preview";

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

const answer = (body: PreviewIn): Preview => ({
  transaction: { ...body.transaction!, gross_amount: "x", total_amount: body.transaction!.quantity },
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
    const { calls } = stubFetch({ [PREVIEW]: (call) => ({ body: answer(call.body as PreviewIn) }) });
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

describe("previewNow", () => {
  it("always asks again, so Save sends what the server completed for this input", async () => {
    const { calls } = stubFetch({ [PREVIEW]: (call) => ({ body: answer(call.body as PreviewIn) }) });
    const { client } = setup();
    await previewNow(client, buy("3"));
    const second = await previewNow(client, buy("3"));
    expect(calls).toHaveLength(2);
    expect(second.transaction?.total_amount).toBe("3");
  });
});

describe("error details", () => {
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
