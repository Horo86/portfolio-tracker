import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, ImportIn, ImportPreview } from "../api/types";
import { MAX_FILE_BYTES } from "../imports";
import { type Call, type Handler, stubFetch } from "../test/fetchStub";
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

function setup(preview: Handler, path = "/import") {
  const stub = stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [EUR_ACCOUNT, USD_ACCOUNT] },
    "GET /api/v1/currencies": { body: ["EUR", "USD"] },
    [PREVIEW]: preview,
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
    expect(previews(calls)).toHaveLength(1);
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
    expect(await screen.findByText(/Other imports are running on the server: trying again in 1 s \(attempt 1 of 3\)/)).toBeInTheDocument();
    expect(await screen.findByText("Format: Directa (Excel) · 22 rows read", {}, { timeout: 2500 })).toBeInTheDocument();
  });
});
