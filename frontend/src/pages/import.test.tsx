import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  Account,
  ImportBatch,
  ImportConfirmIn,
  ImportConfirmOut,
  ImportIn,
  ImportLine,
  ImportPreview,
} from "../api/types";
import { MAX_FILE_BYTES } from "../imports";
import { type Call, type Handler, type Reply, stubFetch } from "../test/fetchStub";
import { HEALTH, ME, renderApp } from "../test/renderApp";

// Invented accounts, files and figures only.
const EUR_ACCOUNT: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto Esempio",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 0,
  currency_locked: false,
};
const USD_ACCOUNT: Account = { ...EUR_ACCOUNT, id: "00000000-0000-4000-8000-0000000000a2", name: "Conto Dollari", currency: "USD" };

const PREVIEW = "POST /api/v1/imports/preview";

function emptyPreview(body: ImportIn, extra: Partial<ImportPreview> = {}): ImportPreview {
  return {
    format: "directa-xlsx",
    file_name: body.file_name ?? null,
    rows_read: 22,
    move_cash: body.move_cash,
    account_code: "B0000",
    counts: {
      selected: 0,
      to_import: 0,
      to_review: 0,
      skipped_by_choice: 0,
      duplicates: 0,
      not_recognized: 0,
      ambiguous: 0,
      invalid: 0,
    },
    accounts: [
      { key: null, account_id: body.account_id ?? EUR_ACCOUNT.id, new: false, name: EUR_ACCOUNT.name, currency: "EUR", provider: null },
    ],
    assets: [],
    lines: [],
    notices: [],
    blocking: [],
    warnings: [],
    fingerprint: "f".repeat(64),
    ...extra,
  };
}

function setup(preview: Handler, path = "/import", extra: Record<string, Handler> = {}) {
  const stub = stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [EUR_ACCOUNT, USD_ACCOUNT] },
    "GET /api/v1/currencies": { body: ["EUR", "USD"] },
    [PREVIEW]: preview,
    ...extra,
  });
  renderApp(path);
  return stub;
}

const previews = (calls: Call[]) =>
  calls.filter((c) => c.path === "/api/v1/imports/preview").map((c) => c.body as ImportIn);

async function chooseFile(content = "a,b\n", name = "esempio.xlsx") {
  const input = await screen.findByLabelText("File to import");
  fireEvent.change(input, { target: { files: [new File([content], name)] } });
}

describe("the import page, file step (mockup 07)", () => {
  it("is in the menu and shows the file step with the known limits", async () => {
    setup({ body: {} });
    expect(await screen.findByRole("heading", { level: 1, name: "Import transactions" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main menu" });
    expect(within(nav).getByRole("link", { name: "Import" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("list", { name: "Import steps" })).toHaveTextContent("1. File");
    expect(screen.getByText("Known limits")).toBeInTheDocument();
    expect(screen.getByText(/Sales, withdrawals, bond redemptions/)).toBeInTheDocument();
    expect(screen.queryByText(/already knows another user's transaction/)).toBeNull();
  });

  it("refuses a file over 10 MB without reading or sending it", async () => {
    const { calls } = setup({ body: {} });
    const big = new File(["x"], "grande.xlsx");
    Object.defineProperty(big, "size", { value: MAX_FILE_BYTES + 1 });
    fireEvent.change(await screen.findByLabelText("File to import"), { target: { files: [big] } });
    expect(await screen.findByText(/larger than 10 MB/)).toHaveAttribute("role", "alert");
    fireEvent.click(screen.getByRole("button", { name: "Continue to the preview" }));
    expect(screen.getByText(/larger than 10 MB/)).toBeInTheDocument();
    expect(previews(calls)).toEqual([]);
  });

  it("asks for a file before continuing", async () => {
    setup({ body: {} });
    fireEvent.click(await screen.findByRole("button", { name: "Continue to the preview" }));
    expect(screen.getByText("Choose a file first.")).toHaveAttribute("role", "alert");
  });

  it("takes a dropped file", async () => {
    const { calls } = setup((call) => ({ body: emptyPreview(call.body as ImportIn) }));
    const zone = (await screen.findByText("Drop the file here")).parentElement!;
    fireEvent.drop(zone, { dataTransfer: { files: [new File(["x;y"], "lasciato.csv")] } });
    expect(screen.getByText(/Chosen file: lasciato.csv/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue to the preview" }));
    await waitFor(() => expect(previews(calls)).toHaveLength(1));
    expect(previews(calls)[0]).toMatchObject({ file_name: "lasciato.csv", content_base64: btoa("x;y") });
  });

  it("sends the file as base64 with the format and the option", async () => {
    const { calls } = setup((call) => ({ body: emptyPreview(call.body as ImportIn) }));
    await chooseFile();
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "directa-xlsx" } });
    fireEvent.click(screen.getByLabelText("Do not move cash"));
    fireEvent.click(screen.getByRole("button", { name: "Continue to the preview" }));
    expect(await screen.findByText("Format: Directa (Excel) · 22 rows read")).toBeInTheDocument();
    expect(previews(calls)[0]).toEqual({
      content_base64: btoa("a,b\n"),
      file_name: "esempio.xlsx",
      format: "directa-xlsx",
      move_cash: false,
      account_id: null,
      accounts: {},
      assets: {},
      selected: null,
    });
  });
});

describe("the account step", () => {
  const accountRequired = (call: Call) =>
    (call.body as ImportIn).account_id
      ? { body: emptyPreview(call.body as ImportIn) }
      : { status: 422, body: { code: "IMPORT_ACCOUNT_REQUIRED", detail: "x" } };

  it("offers the accounts in euro, preselects the one in view and previews into it", async () => {
    const { calls } = setup(accountRequired, `/import?account=${EUR_ACCOUNT.id}`);
    await chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Continue to the preview" }));
    const select = await screen.findByLabelText("Import into account");
    expect(within(select).queryByText("Conto Dollari")).toBeNull();
    expect(select).toHaveValue(EUR_ACCOUNT.id);
    expect(screen.getByRole("list", { name: "Import steps" }).querySelector("[aria-current]")).toHaveTextContent("2. Account");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("Format: Directa (Excel) · 22 rows read")).toBeInTheDocument();
    expect(previews(calls).at(-1)?.account_id).toBe(EUR_ACCOUNT.id);
  });

  it("asks to choose when no account is in view", async () => {
    const { calls } = setup(accountRequired);
    await chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Continue to the preview" }));
    expect(await screen.findByLabelText("Import into account")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Choose the account to import into.")).toBeInTheDocument();
    expect(screen.getByLabelText("Import into account")).toHaveAccessibleDescription(
      /Choose the account to import into\.$/,
    );
    expect(previews(calls)).toHaveLength(1);
  });

  it("creates a new account in euro only", async () => {
    setup(accountRequired);
    await chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Continue to the preview" }));
    fireEvent.click(await screen.findByRole("button", { name: "New account…" }));
    const dialog = await screen.findByRole("dialog", { name: "New account" });
    const currency = within(dialog).getByLabelText("Currency");
    expect(currency).toHaveValue("EUR");
    expect(currency).toBeDisabled();
    expect(currency).toHaveAccessibleDescription("A Directa file goes into an account in euro.");
  });
});

describe("a file that cannot be previewed", () => {
  it("says why, with the column, and lets the user choose another file", async () => {
    setup({ status: 422, body: { code: "IMPORT_FILE_INVALID", detail: "secret detail", column: "colonna_x" } });
    await chooseFile("x", "esempio.csv");
    fireEvent.click(screen.getByRole("button", { name: "Continue to the preview" }));
    const alert = (await screen.findByText(/The file cannot be read/)).closest("[role=alert]")!;
    expect(alert).toHaveTextContent("The file cannot be read");
    expect(alert).toHaveTextContent("Column: «colonna_x».");
    expect(alert).not.toHaveTextContent("secret detail");
    fireEvent.click(within(alert as HTMLElement).getByRole("button", { name: "Choose another file" }));
    expect(await screen.findByText("Drop the file here")).toBeInTheDocument();
  });

  it("shows the wait while the server is busy, then the preview", async () => {
    let busy = 1;
    setup((call) =>
      busy-- > 0
        ? { status: 503, body: { code: "IMPORT_BUSY" }, headers: { "Retry-After": "1" } }
        : { body: emptyPreview(call.body as ImportIn) },
    );
    await chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Continue to the preview" }));
    const countdown = await screen.findByText(/Other imports are running on the server: trying again in 1 s \(attempt 1 of 3\)/);
    // Screen readers hear the attempt once: the seconds that change are hidden from them.
    expect(countdown.closest("[aria-hidden=true]")).not.toBeNull();
    expect(countdown.closest("[role=status]")).toHaveTextContent(
      "Other imports are running on the server: trying again in a few seconds (attempt 1 of 3).",
    );
    expect(await screen.findByText("Format: Directa (Excel) · 22 rows read")).toBeInTheDocument();
  });
});

// --- The preview (mockup 08) -------------------------------------------------------------

const NEW_ETF = "isin:ZZ0000001014";

function previewLine(key: string, fields: Partial<ImportLine>): ImportLine {
  return {
    key,
    category: "TO_IMPORT",
    selectable: true,
    selected: true,
    lines: [20],
    reasons: [],
    info: [],
    account: null,
    asset: null,
    type: "DEPOSIT",
    trade_date: "2024-01-10",
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
    ...fields,
  };
}

const LINES: ImportLine[] = [
  previewLine("order:T1", {
    type: "BUY",
    trade_date: "2024-01-15",
    asset: NEW_ETF,
    quantity: "10",
    gross_amount: "999.00",
    total_amount: "1001.50",
    lines: [30, 31],
    info: [{ code: "MERGED_EXECUTIONS", params: { count: "2" } }],
  }),
  previewLine("protocol:9", {
    type: "WITHDRAWAL",
    trade_date: "2024-04-02",
    total_amount: "6.20",
    account_expense: true,
    lines: [18],
    info: [{ code: "ACCOUNT_EXPENSE", params: { original_type: "Spesa esempio" } }],
  }),
  previewLine("order:T2", {
    category: "TO_REVIEW",
    selected: false,
    type: "BUY",
    trade_date: "2024-03-15",
    asset: NEW_ETF,
    quantity: "5",
    total_amount: "500",
    reasons: [
      {
        code: "UNCONFIRMED_MOVEMENT_TYPE",
        params: { original_type: "Movimento esempio", mapped_as: "TAX_CREDIT" },
      },
    ],
  }),
  previewLine("protocol:1", {
    category: "DUPLICATE",
    selectable: false,
    selected: false,
    reasons: [{ code: "ALREADY_PRESENT", params: { match: "external_ref_and_type" } }],
  }),
  previewLine("line:13", {
    category: "NOT_RECOGNIZED",
    selectable: false,
    selected: false,
    type: null,
    trade_date: null,
    total_amount: null,
    lines: [13],
    reasons: [
      { code: "MOVEMENT_TYPE_NOT_VERIFIED", params: { original_type: "Operazione esempio" } },
    ],
  }),
];

/** An invented preview that follows the request: selection, option and asset choice. */
function fullPreview(body: ImportIn, lines: ImportLine[] = LINES): ImportPreview {
  const chosen = body.assets?.[NEW_ETF]?.asset_type ?? null;
  const shown = lines.map((l) => {
    const selected = body.selected ? body.selected.includes(l.key) : l.category === "TO_IMPORT";
    return { ...l, selected: l.selectable && selected, affects_cash: body.move_cash };
  });
  return emptyPreview(body, {
    counts: {
      selected: shown.filter((l) => l.selected).length,
      to_import: shown.filter((l) => l.category === "TO_IMPORT").length,
      to_review: 1,
      skipped_by_choice: 0,
      duplicates: 1,
      not_recognized: 1,
      ambiguous: 0,
      invalid: 0,
    },
    assets: [
      {
        key: NEW_ETF,
        asset_id: null,
        new: true,
        name: "Esempio Nuovo ETF",
        isin: "ZZ0000001014",
        ticker: null,
        asset_type: chosen,
        quote_currency: "EUR",
        quote_unit: chosen ? "CURRENCY" : null,
        options: [],
      },
    ],
    lines: shown,
    notices: [
      { code: "ACCOUNT_CODE_DIFFERS", params: { file_code: "B0000", account_code: "B9999" } },
      {
        code: "MANUAL_OVERLAP",
        params: { account: "", count: "3", first_date: "2024-01-10", last_date: "2024-04-02" },
      },
    ],
    blocking: chosen ? [] : [{ code: "ASSET_TYPE_REQUIRED", params: { asset: NEW_ETF } }],
    warnings: [
      { code: "NEGATIVE_CASH", date: "2024-01-15", account: null, line: "order:T1", transaction_id: null, introduced: true },
      { code: "FUTURE_DATE", date: "2027-01-01", account: null, line: null, transaction_id: "x", introduced: false },
    ],
  });
}

async function openPreview(handler: Handler, extra: Record<string, Handler> = {}) {
  const stub = setup(handler, "/import", extra);
  await chooseFile();
  fireEvent.click(screen.getByRole("button", { name: "Continue to the preview" }));
  await screen.findByRole("tablist", { name: "Lines by category" });
  return stub;
}

const echoPreview = (call: Call) => ({ body: fullPreview(call.body as ImportIn) });

describe("the preview (mockup 08)", () => {
  it("shows the counts, what blocks, the warnings and one tab per category", async () => {
    await openPreview(echoPreview);
    const counts = screen.getByText("Transactions to import").parentElement!;
    expect(counts).toHaveTextContent("2");
    expect(screen.getByRole("heading", { name: "To solve before confirming" }).parentElement).toHaveTextContent(
      "Choose the type of Esempio Nuovo ETF.",
    );
    const warnings = screen.getByRole("heading", { name: "Warnings" }).parentElement!;
    const items = within(warnings).getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toMatch(/^Conto Esempio already has transactions entered without a broker reference/);
    expect(items[1]).toMatch(/^The file is of the Directa account B0000/);
    expect(items[2]).toBe("The cash of Conto Esempio goes below zero on Jan 15, 2024. (rows 30, 31)");
    expect(warnings).toHaveTextContent("1 warning was already present in the account before this import.");
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "To import (2)",
      "To review (1)",
      "Already present (1)",
      "Not recognized (1)",
    ]);
  });

  it("lists a line with its amounts, cash and notes", async () => {
    await openPreview(echoPreview);
    const row = screen.getByRole("checkbox", { name: /Import: Buy, Esempio Nuovo ETF, Jan 15, 2024/ }).closest("tr")!;
    expect(row).toHaveTextContent("€999.00");
    expect(row).toHaveTextContent("-€1,001.50");
    expect(row).toHaveTextContent("2 executions of the same order, merged.");
    expect(row).toHaveTextContent("rows 30, 31");
  });

  it("asks a new preview with the type chosen, which solves the block", async () => {
    const { calls } = await openPreview(echoPreview);
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "ETF" } });
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "To solve before confirming" })).toBeNull(),
    );
    expect(previews(calls).at(-1)?.assets).toEqual({ [NEW_ETF]: { asset_type: "ETF", quote_unit: null } });
    expect(screen.getByLabelText("Price unit")).toHaveValue("CURRENCY");
  });

  it("moves between tabs with the arrow keys; a tab that cannot be imported has no checkbox", async () => {
    await openPreview(echoPreview);
    const first = screen.getByRole("tab", { name: "To import (2)" });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowLeft" });
    const last = screen.getByRole("tab", { name: "Not recognized (1)" });
    expect(last).toHaveAttribute("aria-selected", "true");
    expect(last).toHaveFocus();
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).queryByRole("checkbox")).toBeNull();
    expect(panel).toHaveTextContent(
      "The movement type «Operazione esempio» is not supported yet: enter it by hand.",
    );
    expect(panel).toHaveTextContent("row 13");
    fireEvent.keyDown(last, { key: "Home" });
    expect(first).toHaveAttribute("aria-selected", "true");
  });

  it("asks a new preview with the ticks, and keeps them after an asset choice", async () => {
    const { calls } = await openPreview(echoPreview);
    fireEvent.click(screen.getByRole("tab", { name: "To review (1)" }));
    const review = screen.getByRole("checkbox", { name: /Import: Buy, Esempio Nuovo ETF, Mar 15, 2024/ });
    expect(review).not.toBeChecked();
    fireEvent.click(review);
    expect(review).toBeChecked(); // at once, before the new preview
    await waitFor(() =>
      expect(previews(calls).at(-1)?.selected).toEqual(["order:T1", "protocol:9", "order:T2"]),
    );
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "STOCK" } });
    await waitFor(() => expect(previews(calls).at(-1)?.assets?.[NEW_ETF]?.asset_type).toBe("STOCK"));
    expect(previews(calls).at(-1)?.selected).toEqual(["order:T1", "protocol:9", "order:T2"]);
  });

  it("unticks the account expenses together, and resets the ticks with the option", async () => {
    const { calls } = await openPreview(echoPreview);
    fireEvent.click(screen.getByLabelText("Account expenses (1)"));
    await waitFor(() => expect(previews(calls).at(-1)?.selected).toEqual(["order:T1"]));
    fireEvent.click(screen.getByLabelText("Do not move cash"));
    await waitFor(() => expect(previews(calls).at(-1)).toMatchObject({ move_cash: false, selected: null }));
    await waitFor(() => expect(screen.getAllByText("does not move cash").length).toBeGreaterThan(0));
  });

  it("ticks and unticks a whole tab", async () => {
    const { calls } = await openPreview(echoPreview);
    fireEvent.click(screen.getByRole("button", { name: "Untick all" }));
    await waitFor(() => expect(previews(calls).at(-1)?.selected).toEqual([]));
    fireEvent.click(screen.getByRole("button", { name: "Tick all (2)" }));
    await waitFor(() => expect(previews(calls).at(-1)?.selected).toEqual(["order:T1", "protocol:9"]));
  });

  it("pages a long tab by 50 lines", async () => {
    const many = Array.from({ length: 60 }, (_, i) =>
      previewLine(`protocol:${100 + i}`, { lines: [100 + i], total_amount: `${i + 1}.00` }),
    );
    await openPreview((call) => ({ body: fullPreview(call.body as ImportIn, many) }));
    expect(screen.getByText("Rows 1–50 of 60")).toBeInTheDocument();
    expect(within(screen.getByRole("tabpanel")).getAllByRole("checkbox")).toHaveLength(50);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Rows 51–60 of 60")).toBeInTheDocument();
    expect(within(screen.getByRole("tabpanel")).getAllByRole("checkbox")).toHaveLength(10);
  });

  it("lets the user choose among their securities with the same ticker", async () => {
    const ids = ["00000000-0000-4000-8000-0000000000b1", "00000000-0000-4000-8000-0000000000b2"];
    const asset = (id: string, name: string) => ({
      id,
      isin: null,
      ticker: "ZZESE",
      name,
      asset_type: "STOCK",
      quote_currency: "EUR",
      quote_unit: "CURRENCY",
      shared: false,
      in_use: true,
      created_at: "2026-01-10T09:00:00Z",
    });
    const handler = (call: Call) => {
      const body = call.body as ImportIn;
      const picked = body.assets?.["ticker:ZZESE"]?.asset_id;
      return {
        body: emptyPreview(body, {
          format: "app-csv",
          lines: [previewLine("line:2", { type: "BUY", asset: "ticker:ZZESE", quantity: "1" })],
          assets: [
            {
              key: "ticker:ZZESE",
              asset_id: picked ?? null,
              new: !picked,
              name: picked ? "Esempio Due SpA" : "ZZESE",
              isin: null,
              ticker: "ZZESE",
              asset_type: picked ? "STOCK" : null,
              quote_currency: "EUR",
              quote_unit: picked ? "CURRENCY" : null,
              options: ids,
            },
          ],
          blocking: picked
            ? []
            : [{ code: "ASSET_CHOICE_REQUIRED", params: { asset: "ticker:ZZESE" } }],
        }),
      };
    };
    const { calls } = await openPreview(handler, {
      "GET /api/v1/assets": { body: [asset(ids[0], "Esempio Uno SpA"), asset(ids[1], "Esempio Due SpA")] },
    });
    expect(screen.getByText(/Several of your securities have the ticker of ZZESE/)).toBeInTheDocument();
    // Matched by ticker, it may not be new: the card names the file's securities.
    expect(screen.getByRole("heading", { name: "Securities in the file" })).toBeInTheDocument();
    const select = screen.getByLabelText("Security");
    expect(select).toHaveValue("");
    await waitFor(() =>
      expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
        "Choose…",
        "Esempio Uno SpA",
        "Esempio Due SpA",
        "A new security",
      ]),
    );
    expect(screen.queryByLabelText("Type")).toBeNull();
    fireEvent.change(select, { target: { value: ids[1] } });
    await waitFor(() => expect(screen.getByLabelText("Security")).toHaveValue(ids[1]));
    expect(previews(calls).at(-1)?.assets).toEqual({ "ticker:ZZESE": { asset_id: ids[1] } });
    expect(screen.queryByText(/Several of your securities/)).toBeNull();
  });
});

// --- The confirmation and the summary (mockup 09) ----------------------------------------

const CONFIRM = "POST /api/v1/imports/confirm";
const confirms = (calls: Call[]) =>
  calls.filter((c) => c.path === "/api/v1/imports/confirm").map((c) => c.body as ImportConfirmIn);

function batch(fields: Partial<ImportBatch>): ImportBatch {
  return {
    id: "00000000-0000-4000-8000-0000000000c1",
    account_id: EUR_ACCOUNT.id,
    format: "directa-xlsx",
    file_name: "esempio.xlsx",
    move_cash: true,
    imported: 2,
    duplicates: 1,
    not_recognized: 1,
    to_review_skipped: 1,
    ambiguous: 0,
    invalid: 0,
    skipped_by_choice: 0,
    transactions: 2,
    created_at: "2026-10-22T10:00:00Z",
    ...fields,
  };
}

function confirmed(batches: ImportBatch[], extra: Partial<ImportConfirmOut> = {}): Reply {
  return {
    body: {
      batches,
      transactions: [],
      accounts_created: [],
      assets_created: ["00000000-0000-4000-8000-0000000000d1"],
      warnings: [
        { code: "NEGATIVE_CASH", date: "2024-01-15", account: null, line: "order:T1", transaction_id: "t", introduced: true },
        { code: "FUTURE_DATE", date: "2027-01-01", account: null, line: null, transaction_id: "x", introduced: false },
      ],
      ...extra,
    },
  };
}

const confirmButton = (count: number) =>
  screen.getByRole("button", { name: `Confirm the import of ${count} transactions` });

async function readyToConfirm(extra: Record<string, Handler>) {
  const stub = await openPreview(echoPreview, extra);
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "ETF" } });
  await waitFor(() => expect(confirmButton(2)).toBeEnabled());
  return stub;
}

describe("the confirmation and the summary (mockup 09)", () => {
  it("cannot confirm while something blocks, or with nothing ticked", async () => {
    await openPreview(echoPreview);
    expect(screen.getByText("2 transactions will be added to the account «Conto Esempio». 1 already present will be skipped.")).toBeInTheDocument();
    expect(confirmButton(2)).toBeDisabled();
    expect(screen.getByText("Solve first what the red card lists.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "ETF" } });
    await waitFor(() => expect(confirmButton(2)).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Untick all" }));
    expect(confirmButton(2)).toBeDisabled(); // a new preview is on its way
    await waitFor(() => expect(confirmButton(0)).toBeDisabled());
    expect(await screen.findByText("Nothing to import: tick at least one line.")).toBeInTheDocument();
  });

  it("sends the preview's fingerprint, selection and choices once, then shows the summary", async () => {
    const { calls } = await readyToConfirm({ [CONFIRM]: confirmed([batch({})]) });
    fireEvent.click(confirmButton(2));
    fireEvent.click(confirmButton(2)); // a double click sends one confirmation
    expect(await screen.findByRole("heading", { name: "Done: 2 transactions added to «Conto Esempio»" })).toBeInTheDocument();
    expect(confirms(calls)).toHaveLength(1);
    expect(confirms(calls)[0]).toMatchObject({
      fingerprint: "f".repeat(64),
      selected: ["order:T1", "protocol:9"],
      file_name: "esempio.xlsx",
      move_cash: true,
      assets: { [NEW_ETF]: { asset_type: "ETF", quote_unit: null } },
    });
    const added = screen.getByText("Added").parentElement!;
    expect(added).toHaveTextContent("2");
    expect(screen.getByText("Warnings to check").parentElement).toHaveTextContent("1");
    expect(screen.getByText("1 line to review was not ticked and was not imported.")).toBeInTheDocument();
    expect(screen.getByText("1 new security: its automatic prices are being looked up.")).toBeInTheDocument();
    expect(screen.getByText("The cash of Conto Esempio goes below zero on Jan 15, 2024. (rows 30, 31)")).toBeInTheDocument();
    expect(screen.getByText("1 warning was already present in the account before this import.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the transactions" })).toHaveAttribute(
      "href",
      `/transactions?account=${EUR_ACCOUNT.id}`,
    );
    expect(screen.getByRole("list", { name: "Import steps" }).querySelector("[aria-current]")).toHaveTextContent("4. Done");
    fireEvent.click(screen.getByRole("button", { name: "Import another file" }));
    expect(await screen.findByText("Drop the file here")).toBeInTheDocument();
  });

  it("previews again when the data changed, keeping the ticks", async () => {
    let stale = true;
    const { calls } = await readyToConfirm({
      [CONFIRM]: () =>
        stale ? ((stale = false), { status: 409, body: { code: "IMPORT_PREVIEW_STALE", detail: "x" } }) : confirmed([batch({})]),
    });
    fireEvent.click(screen.getByRole("tab", { name: "To review (1)" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Import: Buy, Esempio Nuovo ETF, Mar 15, 2024/ }));
    await waitFor(() => expect(confirmButton(3)).toBeEnabled());
    const asked = previews(calls).length;
    fireEvent.click(confirmButton(3));
    expect(await screen.findByText("Your data changed since the preview: check it again before confirming.")).toBeInTheDocument();
    await waitFor(() => expect(previews(calls).length).toBe(asked + 1));
    expect(previews(calls).at(-1)?.selected).toEqual(["order:T1", "protocol:9", "order:T2"]);
    await waitFor(() => expect(confirmButton(3)).toBeEnabled());
    fireEvent.click(confirmButton(3));
    expect(await screen.findByRole("heading", { name: /^Done: 2 transactions/ })).toBeInTheDocument();
  });

  it("shows a refused confirmation without the server's detail, and previews again", async () => {
    const { calls } = await readyToConfirm({ [CONFIRM]: { status: 409, body: { code: "QUANTITY_CONFLICT", detail: "secret detail" } } });
    const asked = previews(calls).length;
    fireEvent.click(confirmButton(2));
    const alert = (await screen.findByText(/a quantity would become negative/)).closest("[role=alert]")!;
    expect(alert).not.toHaveTextContent("secret detail");
    await waitFor(() => expect(previews(calls)).toHaveLength(asked + 1));
    await waitFor(() => expect(confirmButton(2)).toBeEnabled());
  });

  it("waits while the server is busy, then confirms", async () => {
    let busy = 1;
    const { calls } = await readyToConfirm({
      [CONFIRM]: () =>
        busy-- > 0
          ? { status: 503, body: { code: "IMPORT_BUSY" }, headers: { "Retry-After": "1" } }
          : confirmed([batch({})]),
    });
    fireEvent.click(confirmButton(2));
    expect(await screen.findByText(/trying again in 1 s \(attempt 1 of 3\)/)).toBeInTheDocument();
    // The preview being confirmed cannot change meanwhile (inert in the browser, M1).
    const untick = screen.getByRole("button", { name: "Untick all" });
    expect(untick.closest("[inert]")).not.toBeNull();
    fireEvent.click(untick);
    expect(screen.getByRole("checkbox", { name: /Import: Buy, Esempio Nuovo ETF, Jan 15, 2024/ })).toBeChecked();
    expect(await screen.findByRole("heading", { name: /^Done: 2 transactions/ })).toBeInTheDocument();
    expect(confirms(calls)).toHaveLength(2);
  });

  it("stops a confirmation waiting for a busy server with Cancel", async () => {
    const { calls } = await readyToConfirm({
      [CONFIRM]: { status: 503, body: { code: "IMPORT_BUSY" }, headers: { "Retry-After": "1" } },
    });
    fireEvent.click(confirmButton(2));
    expect(await screen.findByText(/trying again in 1 s/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Drop the file here")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 1300));
    expect(confirms(calls)).toHaveLength(1);
    expect(screen.getByText("Drop the file here")).toBeInTheDocument();
  });

  it("keeps Cancel off while a confirmation is on its way", async () => {
    let answer: (reply: Reply) => void = () => {};
    await readyToConfirm({ [CONFIRM]: () => new Promise<Reply>((resolve) => (answer = resolve)) });
    fireEvent.click(confirmButton(2));
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled());
    answer(confirmed([batch({})]));
    expect(await screen.findByRole("heading", { name: /^Done: 2 transactions/ })).toBeInTheDocument();
  });
});

// --- The accounts of a Portfolio Tracker CSV ---------------------------------------------

const NEW_ACCOUNT: Account = { ...EUR_ACCOUNT, id: "00000000-0000-4000-8000-0000000000a3", name: "Conto CSV" };

/** An invented CSV preview: accounts «Conto CSV» (no currency) and «Conto Due» (EUR). */
function csvPreview(body: ImportIn): ImportPreview {
  const target = (name: string, currency: string | null) => {
    const id = body.accounts?.[name] ?? null;
    const known = [EUR_ACCOUNT, NEW_ACCOUNT].find((a) => a.id === id);
    return known
      ? { key: name, account_id: known.id, new: false, name: known.name, currency: known.currency, provider: null }
      : { key: name, account_id: null, new: true, name, currency, provider: null };
  };
  const accounts = [target("Conto CSV", null), target("Conto Due", "EUR")];
  const lines = [
    previewLine("line:2", { account: "Conto CSV", lines: [2] }),
    previewLine("line:3", { account: "Conto Due", lines: [3] }),
  ];
  return emptyPreview(body, {
    format: "app-csv",
    account_code: null,
    counts: { ...emptyPreview(body).counts, selected: 2, to_import: 2 },
    accounts,
    lines,
    blocking: accounts[0].currency
      ? []
      : [{ code: "ACCOUNT_CURRENCY_REQUIRED", params: { account: "Conto CSV" } }],
  });
}

describe("the accounts of a CSV file", () => {
  it("creates the account without a currency, named as in the file, and previews into it", async () => {
    let created = false;
    const { calls } = await openPreview((call) => ({ body: csvPreview(call.body as ImportIn) }), {
      "POST /api/v1/accounts": () => ((created = true), { status: 201, body: NEW_ACCOUNT }),
      "GET /api/v1/accounts": () => ({ body: created ? [EUR_ACCOUNT, NEW_ACCOUNT] : [EUR_ACCOUNT] }),
    });
    expect(screen.getByText(/The account «Conto CSV» of the file has no currency/)).toBeInTheDocument();
    expect(screen.getByLabelText("«Conto CSV» goes into")).toHaveValue("new");
    expect(screen.getByLabelText("«Conto Due» goes into")).toHaveDisplayValue("A new account «Conto Due» (EUR)");
    expect(screen.getByText("2 transactions will be added to 2 accounts.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create the account «Conto CSV»…" }));
    const dialog = await screen.findByRole("dialog", { name: "New account" });
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Conto CSV");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(previews(calls).at(-1)?.accounts).toEqual({ "Conto CSV": NEW_ACCOUNT.id }));
    expect(calls.find((c) => c.path === "/api/v1/accounts" && c.method === "POST")?.body).toMatchObject({
      name: "Conto CSV",
      currency: "EUR",
    });
    await waitFor(() => expect(screen.queryByText(/has no currency/)).toBeNull());
    expect(screen.getByLabelText("«Conto CSV» goes into")).toHaveValue(NEW_ACCOUNT.id);
    // The button that opened the dialog is gone: the focus is on the account's choice.
    expect(screen.getByLabelText("«Conto CSV» goes into")).toHaveFocus();
  });

  it("sends a file account to one of the user's accounts, or back to a new one", async () => {
    const { calls } = await openPreview((call) => ({ body: csvPreview(call.body as ImportIn) }));
    fireEvent.change(screen.getByLabelText("«Conto Due» goes into"), { target: { value: EUR_ACCOUNT.id } });
    await waitFor(() => expect(previews(calls).at(-1)?.accounts).toEqual({ "Conto Due": EUR_ACCOUNT.id }));
    await waitFor(() => expect(screen.getByLabelText("«Conto Due» goes into")).toHaveValue(EUR_ACCOUNT.id));
    fireEvent.change(screen.getByLabelText("«Conto Due» goes into"), { target: { value: "new" } });
    await waitFor(() => expect(previews(calls).at(-1)?.accounts).toEqual({ "Conto Due": null }));
  });

  it("sums up an import into several accounts, one row each", async () => {
    const created = { ...EUR_ACCOUNT, id: "00000000-0000-4000-8000-0000000000a4", name: "Conto Due" };
    let done = false;
    const csv = (call: Call) => ({
      body: csvPreview({ ...(call.body as ImportIn), accounts: { "Conto CSV": NEW_ACCOUNT.id } }),
    });
    await openPreview(csv, {
      "GET /api/v1/accounts": () => ({ body: done ? [NEW_ACCOUNT, created] : [NEW_ACCOUNT] }),
      [CONFIRM]: () => ((done = true), confirmed(
        [
          batch({ id: "b1", account_id: NEW_ACCOUNT.id, format: "app-csv", imported: 1, duplicates: 0 }),
          batch({ id: "b2", account_id: created.id, format: "app-csv", imported: 1, duplicates: 2 }),
        ],
        { accounts_created: [created.id], assets_created: [], warnings: [] },
      )),
    });
    fireEvent.click(confirmButton(2));
    expect(await screen.findByRole("heading", { name: "Done: 2 transactions added to 2 accounts" })).toBeInTheDocument();
    const rows = within(screen.getByRole("table", { name: "Transactions added per account" })).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toEqual(["AccountAddedAlready present, skipped", "Conto CSV10", "Conto Due12"]);
    expect(screen.getByText("1 account created.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the transactions" })).toHaveAttribute("href", "/transactions");
  });
});
