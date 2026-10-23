import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ApiError } from "./api/client";
import type { ImportCategory, ImportIn, ImportLine, ImportPreview } from "./api/types";
import {
  fitsUpload,
  type LoadedFile,
  MAX_FILE_BYTES,
  readFile,
  selectionOf,
  useImport,
  withBusyRetry,
} from "./imports";
import { type Call, type Reply, stubFetch } from "./test/fetchStub";

// Invented keys, names and content only.
const PREVIEW = "POST /api/v1/imports/preview";
const CONFIRM = "POST /api/v1/imports/confirm";
const FILE: LoadedFile = { id: 1, name: "esempio.csv", size: 3, base64: "YWJj" };
const FINGERPRINT = "f".repeat(64);

function line(key: string, category: ImportCategory, selected = false): ImportLine {
  return {
    key,
    category,
    selectable: ["TO_IMPORT", "TO_REVIEW", "SKIPPED_BY_CHOICE"].includes(category),
    selected,
    lines: [2],
    reasons: [],
    info: [],
    account: null,
    asset: null,
    type: "DEPOSIT",
    trade_date: "2026-03-02",
    trade_time: null,
    value_date: null,
    quantity: null,
    unit_price: null,
    gross_amount: null,
    fees: null,
    accrued_interest_paid: null,
    accrued_interest_received: null,
    taxes: null,
    total_amount: "100",
    split_ratio: null,
    affects_cash: true,
    external_ref: null,
    notes: null,
    metadata: null,
    account_expense: false,
    original_isin: null,
    original_name: null,
  };
}

const CATEGORIES: [string, ImportCategory][] = [
  ["a", "TO_IMPORT"],
  ["b", "TO_IMPORT"],
  ["r", "TO_REVIEW"],
  ["d", "DUPLICATE"],
];

/** What the server answers: the lines above, selected as the request asks or by default. */
function answer(body: ImportIn): ImportPreview {
  const lines = CATEGORIES.map(([key, category]) =>
    line(key, category, body.selected ? body.selected.includes(key) : category === "TO_IMPORT"),
  );
  return {
    format: "app-csv",
    file_name: body.file_name ?? null,
    rows_read: lines.length,
    move_cash: body.move_cash,
    account_code: null,
    counts: {
      selected: lines.filter((l) => l.selected).length,
      to_import: 2,
      to_review: 1,
      skipped_by_choice: 0,
      duplicates: 1,
      not_recognized: 0,
      ambiguous: 0,
      invalid: 0,
    },
    accounts: [],
    assets: [],
    lines,
    notices: [],
    blocking: [],
    warnings: [],
    fingerprint: FINGERPRINT,
  };
}

const echo = (call: Call): Reply => ({ body: answer(call.body as ImportIn) });
const busy: Reply = { status: 503, body: { code: "IMPORT_BUSY" }, headers: { "Retry-After": "0" } };

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useImport(), { wrapper });
}

const previews = (calls: Call[]) =>
  calls.filter((c) => c.path === "/api/v1/imports/preview").map((c) => c.body as ImportIn);

describe("the file", () => {
  it("is read as base64 with its name and size", async () => {
    const loaded = await readFile(new File(["a,b\n"], "esempio.csv", { type: "text/csv" }));
    expect(loaded).toMatchObject({ name: "esempio.csv", size: 4, base64: btoa("a,b\n") });
  });

  it("fits the upload up to 10 MB", () => {
    expect(fitsUpload({ size: MAX_FILE_BYTES })).toBe(true);
    expect(fitsUpload({ size: MAX_FILE_BYTES + 1 })).toBe(false);
  });
});

describe("selectionOf", () => {
  it("is the server's default until the user ticks something, then every selectable line", () => {
    const shown = answer({ content_base64: "", move_cash: true });
    expect(selectionOf(shown, new Map())).toBeNull();
    expect(selectionOf(shown, new Map([["r", true]]))).toEqual(["a", "b", "r"]);
    expect(selectionOf(shown, new Map([["a", false], ["d", true]]))).toEqual(["b"]);
  });
});

describe("withBusyRetry", () => {
  it("does not wait nor try again once aborted while an attempt was on its way", async () => {
    const controller = new AbortController();
    let attempts = 0;
    const call = () => {
      attempts += 1;
      controller.abort(); // e.g. the page is left while the request is on its way
      return Promise.reject(new ApiError(503, "IMPORT_BUSY", {}, 1));
    };
    const started = Date.now();
    await expect(withBusyRetry(call, () => {}, controller.signal)).rejects.toBeDefined();
    expect(attempts).toBe(1);
    expect(Date.now() - started).toBeLessThan(500);
  });
});

describe("useImport", () => {
  it("asks the first preview with the default selection and the user's first choices", async () => {
    const { calls } = stubFetch({ [PREVIEW]: echo });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: "app-csv", moveCash: false }));
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(previews(calls)).toEqual([
      {
        content_base64: "YWJj",
        file_name: "esempio.csv",
        format: "app-csv",
        move_cash: false,
        account_id: null,
        accounts: {},
        assets: {},
        selected: null,
      },
    ]);
    expect(calls[0].headers.get("X-Requested-With")).toBe("portfolio-tracker");
  });

  it("keeps the ticks after an asset choice and resets them when the option changes", async () => {
    const { calls } = stubFetch({ [PREVIEW]: echo });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: null, moveCash: true }));
    await waitFor(() => expect(result.current.current).toBe(true));

    act(() => result.current.tick(["r"], true));
    expect(result.current.current).toBe(false); // the shown preview is not for these ticks
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(previews(calls).at(-1)?.selected).toEqual(["a", "b", "r"]);

    act(() => result.current.chooseAsset("isin:ZZ0000002012", { asset_type: "ETF" }));
    await waitFor(() => expect(previews(calls)).toHaveLength(3));
    expect(previews(calls)[2]).toMatchObject({
      selected: ["a", "b", "r"],
      assets: { "isin:ZZ0000002012": { asset_type: "ETF" } },
    });

    act(() => result.current.setMoveCash(false));
    await waitFor(() => expect(previews(calls)).toHaveLength(4));
    expect(previews(calls)[3]).toMatchObject({ selected: null, move_cash: false });
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(previews(calls)).toHaveLength(4); // nothing more to ask
  });

  it("aborts an outdated request and never shows its late answer", async () => {
    let release: (reply: Reply) => void = () => {};
    const { calls } = stubFetch({
      [PREVIEW]: (call) =>
        (call.body as ImportIn).account_id === null
          ? new Promise<Reply>((resolve) => (release = resolve))
          : echo(call),
    });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: "directa-xlsx", moveCash: true }));
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => result.current.setAccount("00000000-0000-4000-8000-0000000000a1"));
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(calls[0].signal?.aborted).toBe(true);

    release({ body: { ...answer(calls[0].body as ImportIn), file_name: "late.xlsx" } });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(result.current.preview?.file_name).toBe("esempio.csv");
    expect(result.current.current).toBe(true);
  });

  it("goes back to the default selection when the server refuses the ticked lines", async () => {
    const { calls } = stubFetch({
      [PREVIEW]: (call) =>
        (call.body as ImportIn).selected?.includes("r")
          ? { status: 422, body: { code: "IMPORT_SELECTION_INVALID", lines: ["r"] } }
          : echo(call),
    });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: null, moveCash: true }));
    await waitFor(() => expect(result.current.current).toBe(true));
    act(() => result.current.tick(["r"], true));
    await waitFor(() => expect(result.current.notice).toBe("selectionReset"));
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(previews(calls).map((b) => b.selected)).toEqual([null, ["a", "b", "r"], null]);
    expect(result.current.ticks.size).toBe(0);
    expect(result.current.error).toBeNull();
  });

  it("tries again after Retry-After while the server is busy, at most three times", async () => {
    let busyLeft = 3;
    const { calls } = stubFetch({
      [PREVIEW]: (call) => (busyLeft-- > 0 ? busy : echo(call)),
    });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: null, moveCash: true }));
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(calls).toHaveLength(4);
    expect(result.current.wait).toBeNull();
  });

  it("stops after three retries, then tries again only when asked", async () => {
    let busyLeft = 4;
    const { calls } = stubFetch({
      [PREVIEW]: (call) => (busyLeft-- > 0 ? busy : echo(call)),
    });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: null, moveCash: true }));
    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError));
    expect((result.current.error as ApiError).code).toBe("IMPORT_BUSY");
    expect(calls).toHaveLength(4);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(calls).toHaveLength(5);
  });

  it("asks the account when the file does not say it", async () => {
    stubFetch({
      [PREVIEW]: (call) =>
        (call.body as ImportIn).account_id
          ? echo(call)
          : { status: 422, body: { code: "IMPORT_ACCOUNT_REQUIRED" } },
    });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: null, moveCash: true }));
    await waitFor(() => expect((result.current.error as ApiError)?.code).toBe("IMPORT_ACCOUNT_REQUIRED"));
    expect(result.current.pending).toBe(false);
    act(() => result.current.setAccount("00000000-0000-4000-8000-0000000000a1"));
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(result.current.error).toBeNull();
  });

  it("confirms exactly the lines shown, once, then lets the file go", async () => {
    const { calls } = stubFetch({
      [PREVIEW]: echo,
      [CONFIRM]: {
        status: 201,
        body: { batches: [], transactions: [], accounts_created: [], assets_created: [], warnings: [] },
      },
    });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: null, moveCash: true }));
    await waitFor(() => expect(result.current.current).toBe(true));
    await act(async () => {
      await Promise.all([result.current.confirm(), result.current.confirm()]);
    });
    const confirms = calls.filter((c) => c.path === "/api/v1/imports/confirm");
    expect(confirms).toHaveLength(1);
    expect(confirms[0].body).toMatchObject({
      content_base64: "YWJj",
      fingerprint: FINGERPRINT,
      selected: ["a", "b"],
    });
    expect(result.current.done).not.toBeNull();
    expect(result.current.file).toBeNull();
  });

  it("keeps the preview as shown while a confirmation waits for a busy server", async () => {
    let busyLeft = 1;
    const { calls } = stubFetch({
      [PREVIEW]: echo,
      [CONFIRM]: () =>
        busyLeft-- > 0
          ? { ...busy, headers: { "Retry-After": "1" } }
          : {
              status: 201,
              body: { batches: [], transactions: [], accounts_created: [], assets_created: [], warnings: [] },
            },
    });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: null, moveCash: true }));
    await waitFor(() => expect(result.current.current).toBe(true));
    let confirming: Promise<void> = Promise.resolve();
    act(() => {
      confirming = result.current.confirm();
    });
    await waitFor(() => expect(result.current.wait).not.toBeNull());
    act(() => result.current.tick(["r"], true));
    expect(result.current.ticks.size).toBe(0);
    act(() => {
      result.current.chooseAsset("isin:ZZ0000002012", { asset_type: "ETF" });
      result.current.setMoveCash(false);
    });
    expect(result.current.choices).toMatchObject({ moveCash: true, assets: {} });
    await act(() => confirming);
    const confirms = calls.filter((c) => c.path === "/api/v1/imports/confirm");
    expect(confirms.map((c) => (c.body as ImportIn).selected)).toEqual([["a", "b"], ["a", "b"]]);
    expect(previews(calls)).toHaveLength(1);
  });

  it("sends nothing more once the page is left while a confirmation waits", async () => {
    const { calls } = stubFetch({
      [PREVIEW]: echo,
      [CONFIRM]: { ...busy, headers: { "Retry-After": "1" } },
    });
    const { result, unmount } = setup();
    act(() => result.current.start(FILE, { format: null, moveCash: true }));
    await waitFor(() => expect(result.current.current).toBe(true));
    act(() => void result.current.confirm());
    await waitFor(() => expect(result.current.wait).not.toBeNull());
    unmount();
    await new Promise((resolve) => setTimeout(resolve, 1300));
    expect(calls.filter((c) => c.path === "/api/v1/imports/confirm")).toHaveLength(1);
  });

  it("asks the preview again when the confirmation finds it out of date", async () => {
    const { calls } = stubFetch({
      [PREVIEW]: echo,
      [CONFIRM]: { status: 409, body: { code: "IMPORT_PREVIEW_STALE" } },
    });
    const { result } = setup();
    act(() => result.current.start(FILE, { format: null, moveCash: true }));
    await waitFor(() => expect(result.current.current).toBe(true));
    await act(() => result.current.confirm());
    expect(result.current.notice).toBe("stale");
    await waitFor(() => expect(previews(calls)).toHaveLength(2));
    await waitFor(() => expect(result.current.current).toBe(true));
    expect(result.current.done).toBeNull();
  });
});
