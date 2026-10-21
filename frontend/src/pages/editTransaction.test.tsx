import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, Asset, Preview, PreviewIn, Transaction } from "../api/types";
import { type Call, type Handler, stubFetch } from "../test/fetchStub";
import { HEALTH, ME, renderApp } from "../test/renderApp";

// Invented accounts, securities and amounts only.
const EUR: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 3,
  currency_locked: true,
};
const WORLD: Asset = {
  id: "00000000-0000-4000-8000-0000000000b1",
  isin: null,
  ticker: null,
  name: "Mondo Azionario ETF",
  asset_type: "ETF",
  quote_currency: "EUR",
  quote_unit: "CURRENCY",
  shared: false,
  in_use: true,
  created_at: "2026-01-10T09:00:00Z",
};
const BUY: Transaction = {
  id: "00000000-0000-4000-8000-0000000000c1",
  sequence: 4,
  created_at: "2026-10-05T09:00:00Z",
  updated_at: "2026-10-05T09:00:00Z",
  account_id: EUR.id,
  type: "BUY",
  trade_date: "2026-10-05",
  trade_time: null,
  value_date: null,
  asset_id: WORLD.id,
  quantity: "15",
  unit_price: "101.80",
  gross_amount: "1527.00",
  fees: "3.00",
  accrued_interest_paid: null,
  accrued_interest_received: null,
  taxes: null,
  total_amount: "1530.00",
  split_ratio: null,
  affects_cash: true,
  external_ref: "ORD-0001",
  metadata: { source: "esempio", row: 7 },
  notes: null,
};
const SELL: Transaction = {
  ...BUY,
  metadata: null,
  id: "00000000-0000-4000-8000-0000000000c2",
  type: "SELL",
  trade_date: "2026-10-10",
  quantity: "60",
  external_ref: null,
};

const ONE = `GET /api/v1/transactions/${BUY.id}`;
const PREVIEW = "POST /api/v1/transactions/preview";
const PUT = `PUT /api/v1/transactions/${BUY.id}`;
const DELETE = `DELETE /api/v1/transactions/${BUY.id}`;
const LIST = "GET /api/v1/transactions?type=BUY&limit=50&offset=0";
const NOTHING = { transactions: [], deleted: [], warnings: [] };

const base: Omit<Preview, "transaction" | "derived"> = {
  account_id: EUR.id,
  currency: "EUR",
  cash: { before: "7498.25", change: "0", after: "7498.25" },
  position: {
    asset_id: WORLD.id,
    quantity_before: "65",
    quantity_after: "65",
    cost_basis_before: "6532.50",
    cost_basis_after: "6532.50",
    pmc_before: "100.50",
    pmc_after: "100.50",
  },
  warnings: [],
};

/** The server's dry run: an edit comes back with its total, a delete with no transaction. */
const dryRun: Handler = (call: Call) => {
  const tx = (call.body as PreviewIn).transaction;
  if (!tx) {
    return {
      body: {
        ...base,
        transaction: null,
        derived: [],
        cash: { before: "7498.25", change: "1530.00", after: "9028.25" },
        position: { ...base.position!, quantity_after: "50" },
      },
    };
  }
  return { body: { ...base, transaction: { ...tx, total_amount: "1531.00" }, derived: [] } };
};

function setup(routes: Record<string, Handler> = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [EUR] },
    "GET /api/v1/assets": { body: [WORLD] },
    [ONE]: { body: BUY },
    [LIST]: { body: { items: [BUY], total: 1 } },
    [PREVIEW]: dryRun,
    ...routes,
  });
}

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

const summary = () => screen.getByRole("complementary", { name: "Summary" });

/** The list filtered on buys, then "Edit" on the row: the edit page with the stored values. */
async function openFromList() {
  const result = renderApp("/transactions?type=BUY");
  const link = await screen.findByRole("link", {
    name: "Edit: Buy, Mondo Azionario ETF, Oct 5, 2026",
  });
  fireEvent.click(link);
  await screen.findByRole("heading", { name: "Edit transaction", level: 1 });
  await waitFor(() => expect(screen.getByLabelText("Quantity")).toHaveValue("15"));
  return result;
}

describe("edit page", () => {
  it("opens with the stored values and saves one edit with the stored gross and reference", async () => {
    const { calls } = setup({ [PUT]: { body: NOTHING } });
    const { router } = await openFromList();
    expect(screen.getByText("Buy of 15 Mondo Azionario ETF · Conto titoli")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Buy" })).toBeChecked();
    expect(screen.getByLabelText("Security")).toHaveValue(WORLD.id);
    expect(screen.getByLabelText("Date")).toHaveValue("2026-10-05");
    expect(screen.getByLabelText("Unit price (EUR)")).toHaveValue("101.80");
    // The stored gross is shown among the other amounts, which are open.
    expect(screen.getByLabelText("Gross amount (EUR)")).toBeVisible();
    expect(screen.getByLabelText("Gross amount (EUR)")).toHaveValue("1527.00");
    expect(screen.queryByRole("button", { name: "Save and add another" })).toBeNull();

    type("Fees (EUR)", "4.00");
    expect(await within(summary()).findByText("Change in cash")).toBeInTheDocument();
    const lastPreview = () => calls.filter((c) => c.path === "/api/v1/transactions/preview").at(-1);
    await waitFor(() =>
      expect((lastPreview()?.body as PreviewIn).transaction).toMatchObject({ fees: "4.00" }),
    );
    expect(lastPreview()?.body).toEqual({
      replaces: BUY.id,
      transaction: {
        account_id: EUR.id,
        type: "BUY",
        trade_date: "2026-10-05",
        asset_id: WORLD.id,
        quantity: "15",
        unit_price: "101.80",
        gross_amount: "1527.00",
        fees: "4.00",
        affects_cash: true,
        external_ref: "ORD-0001",
        metadata: { source: "esempio", row: 7 },
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT");
    // The PUT replaces the whole transaction: the import's reference and metadata go with it.
    expect(put?.body).toMatchObject({
      gross_amount: "1527.00",
      total_amount: "1531.00",
      external_ref: "ORD-0001",
      metadata: { source: "esempio", row: 7 },
    });
    // Back to the list as it was left: the filter is still there.
    expect(router.state.location.pathname).toBe("/transactions");
    expect(router.state.location.search).toBe("?type=BUY");
  });

  it("an exact stored gross of 10 decimals stays as it is: a notes-only edit saves", async () => {
    // What the server stores for 3 × 4.1893000680 (D-139): more decimals than a quantity takes.
    const exact: Transaction = {
      ...BUY,
      quantity: "3",
      unit_price: "4.1893000680",
      gross_amount: "12.5679002040",
      total_amount: "15.5679002040",
    };
    const { calls } = setup({
      [ONE]: { body: exact },
      [PUT]: { body: NOTHING },
      "GET /api/v1/transactions?limit=50&offset=0": { body: { items: [exact], total: 1 } },
    });
    renderApp(`/transactions/${BUY.id}/edit`);
    await waitFor(() => expect(screen.getByLabelText("Quantity")).toHaveValue("3"));
    const gross = screen.getByLabelText("Gross amount (EUR)");
    expect(gross).toHaveValue("12.5679002040");
    expect(screen.getByLabelText("Unit price (EUR)")).toHaveValue("4.1893000680");

    type("Notes (optional)", "Solo la nota");
    expect(await within(summary()).findByText("Change in cash")).toBeInTheDocument();
    expect(gross).not.toHaveAttribute("aria-invalid");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({
      quantity: "3",
      unit_price: "4.1893000680",
      gross_amount: "12.5679002040",
      notes: "Solo la nota",
    });
  });

  it("a new quantity drops the stored gross, which the server computes again", async () => {
    const { calls } = setup();
    await openFromList();
    type("Quantity", "20");
    const gross = screen.getByLabelText("Gross amount (EUR)");
    expect(gross).toHaveValue("");
    expect(gross).toHaveAccessibleDescription(
      expect.stringContaining("Computed again from quantity × price"),
    );
    await waitFor(() => {
      const last = calls.filter((c) => c.path === "/api/v1/transactions/preview").at(-1);
      expect((last?.body as PreviewIn).transaction).toMatchObject({ quantity: "20" });
    });
    const last = calls.filter((c) => c.path === "/api/v1/transactions/preview").at(-1);
    expect((last?.body as PreviewIn).transaction).not.toHaveProperty("gross_amount");
  });

  it("a conflict of the edited transaction is described as typed", async () => {
    const conflict = {
      code: "QUANTITY_CONFLICT",
      detail: "x",
      conflict: {
        reason: "NEGATIVE_QUANTITY",
        transaction_id: BUY.id,
        account_id: EUR.id,
        asset_id: WORLD.id,
        quantity: "-5",
        held: "0",
      },
    };
    setup({ [PREVIEW]: { status: 409, body: conflict }, [PUT]: { status: 409, body: conflict } });
    await openFromList();
    fireEvent.click(screen.getByRole("radio", { name: "Sell" }));
    type("Quantity", "5");
    expect(
      await within(summary()).findByText(
        "Not enough quantity: you would hold 0 before this transaction, and it would leave -5.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("dialog", { name: "This change cannot be saved" });
    expect(dialog).toHaveTextContent(
      "First transaction in conflict: Sell of 5 on Oct 5, 2026 (you hold 0).",
    );
  });

  it("says when the transaction no longer exists", async () => {
    setup({ [ONE]: { status: 404, body: { code: "NOT_FOUND", detail: "x" } } });
    renderApp(`/transactions/${BUY.id}/edit`);
    expect(
      await screen.findByText("This transaction does not exist any more: it may have been deleted."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to transactions" })).toHaveAttribute(
      "href",
      "/transactions",
    );
  });
});

describe("delete dialog", () => {
  it("says what follows from the delete, then deletes and goes back to the list", async () => {
    const { calls } = setup({ [DELETE]: { body: { ...NOTHING, deleted: [BUY.id] } } });
    const { router } = await openFromList();
    fireEvent.click(screen.getByRole("button", { name: "Delete…" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete this transaction?" });
    expect(dialog).toHaveTextContent(
      "Buy of 15 Mondo Azionario ETF on Oct 5, 2026 · Conto titoli. " +
        "Positions, cash and gains are recalculated.",
    );
    expect(await within(dialog).findByText("quantity of Mondo Azionario ETF: 50")).toBeVisible();
    expect(within(dialog).getByText("cash of Conto titoli: €9,028.25")).toBeVisible();
    const deletePreview = calls.find(
      (c) => c.path === "/api/v1/transactions/preview" && !(c.body as PreviewIn).transaction,
    );
    expect(deletePreview?.body).toEqual({ replaces: BUY.id });

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Transaction deleted.")).toBeInTheDocument();
    expect(router.state.location.search).toBe("?type=BUY");
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("a delete that would leave a later sale short opens the conflict dialog", async () => {
    const conflict = {
      code: "QUANTITY_CONFLICT",
      detail: "x",
      conflict: {
        reason: "NEGATIVE_QUANTITY",
        transaction_id: SELL.id,
        account_id: EUR.id,
        asset_id: WORLD.id,
        quantity: "-10",
        held: "50",
      },
    };
    setup({
      [PREVIEW]: (call) =>
        (call.body as PreviewIn).transaction ? dryRun(call) : { status: 409, body: conflict },
      [DELETE]: { status: 409, body: conflict },
      [`GET /api/v1/transactions/${SELL.id}`]: { body: SELL },
    });
    await openFromList();
    fireEvent.click(screen.getByRole("button", { name: "Delete…" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete this transaction?" });
    expect(
      await within(dialog).findByText(
        "A later transaction of this security would be left without enough quantity: " +
          "50 held before it, -10 after.",
      ),
    ).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    const refused = await screen.findByRole("dialog", { name: "This change cannot be saved" });
    expect(
      await within(refused).findByText(
        "First transaction in conflict: Sell of 60 on Oct 10, 2026 (you hold 50).",
      ),
    ).toBeVisible();
    fireEvent.click(within(refused).getByRole("button", { name: "Back to the form" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("heading", { name: "Edit transaction", level: 1 })).toBeVisible();
  });
});
