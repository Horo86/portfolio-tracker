import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, Asset, Preview, PreviewIn, TransactionFields } from "../api/types";
import i18n from "../i18n";
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
const ROBOTICS: Asset = {
  ...WORLD,
  id: "00000000-0000-4000-8000-0000000000b2",
  name: "Robotica USA",
  quote_currency: "USD",
};

const PREVIEW = "POST /api/v1/transactions/preview";
const CREATE = "POST /api/v1/transactions";

/** What the server answers for a BUY of 15 × 101.80 with 3.00 of fees (mockup 05). */
function completed(tx: TransactionFields, extra: Partial<Preview> = {}): Preview {
  return {
    transaction: { ...tx, gross_amount: "1527.00", total_amount: "1530.00" },
    derived: ["gross_amount", "total_amount"],
    account_id: EUR.id,
    currency: "EUR",
    cash: { before: "7498.25", change: "-1530.00", after: "5968.25" },
    position: {
      asset_id: WORLD.id,
      quantity_before: "50",
      quantity_after: "65",
      cost_basis_before: "5002.50",
      cost_basis_after: "6532.50",
      pmc_before: "100.05",
      pmc_after: "100.50",
    },
    warnings: [],
    ...extra,
  };
}

const echo: Handler = (call: Call) => ({
  body: completed((call.body as PreviewIn).transaction!),
});

function setup(routes: Record<string, Handler> = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [EUR] },
    "GET /api/v1/assets": { body: [WORLD, ROBOTICS] },
    "GET /api/v1/transactions?limit=50&offset=0": { body: { items: [], total: 0 } },
    [PREVIEW]: echo,
    ...routes,
  });
}

const type = (label: string | RegExp, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

async function openForm(path = "/transactions/new") {
  const result = renderApp(path);
  await screen.findByRole("heading", { name: "New transaction", level: 1 });
  // The only account is chosen; the list of securities has arrived.
  await screen.findByRole("option", { name: "Mondo Azionario ETF · EUR" });
  return result;
}

async function fillBuy() {
  type("Security", WORLD.id);
  type("Date", "2026-10-12");
  type("Quantity", "15");
  type("Unit price (EUR)", "101.80");
  type("Fees (EUR)", "3.00");
}

const summary = () => screen.getByRole("complementary", { name: "Summary" });

describe("new transaction form", () => {
  it("shows the server's figures as you type and saves exactly what it completed", async () => {
    const { calls } = setup({
      [CREATE]: (call) => ({
        status: 201,
        body: { transactions: [call.body], deleted: [], warnings: [] },
      }),
    });
    const { router } = await openForm();
    expect(screen.getByLabelText("Account")).toHaveValue(EUR.id);
    expect(within(summary()).getByText("Fill in the details to see the figures.")).toBeVisible();

    await fillBuy();
    const box = summary();
    expect(await within(box).findByText("Gross amount (15 × 101.80)")).toBeInTheDocument();
    expect(within(box).getByText("€1,527.00")).toBeInTheDocument();
    expect(within(box).getByText("Total cost").nextSibling).toHaveTextContent("€1,530.00");
    expect(within(box).getByText("Cash movement").nextSibling).toHaveTextContent("-€1,530.00");
    expect(within(box).getByText("Cash after").nextSibling).toHaveTextContent("€5,968.25");
    expect(within(box).getByText("Quantity after").nextSibling).toHaveTextContent("65");
    expect(within(box).getByText("New average cost").nextSibling).toHaveTextContent("€100.50");

    // The browser computes nothing: the gross and total are left to the server.
    const sent = calls.filter((c) => c.path === "/api/v1/transactions/preview");
    expect(sent.at(-1)?.body).toEqual({
      transaction: {
        account_id: EUR.id,
        type: "BUY",
        trade_date: "2026-10-12",
        asset_id: WORLD.id,
        quantity: "15",
        unit_price: "101.80",
        fees: "3.00",
        affects_cash: true,
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Transaction saved.")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/transactions");
    const create = calls.find((c) => c.method === "POST" && c.path === "/api/v1/transactions");
    // Save asked again, then sent the completed transaction as it came back.
    const previews = calls.filter((c) => c.path === "/api/v1/transactions/preview");
    expect(calls.indexOf(previews.at(-1)!)).toBeLessThan(calls.indexOf(create!));
    const typed = (sent.at(-1)!.body as PreviewIn).transaction!;
    expect(create?.body).toEqual({ ...typed, gross_amount: "1527.00", total_amount: "1530.00" });
  });

  it("reads numbers per language and shows them back as read (D-140)", async () => {
    await i18n.changeLanguage("it");
    setup({ "GET /api/v1/me": { body: { ...ME, language: "it" } } });
    renderApp("/transactions/new");
    await screen.findByRole("option", { name: "Mondo Azionario ETF · EUR" });
    type("Titolo", WORLD.id);
    type("Quantità", "1.234");
    type("Prezzo unitario (EUR)", "1.5");
    expect(await screen.findByText("Controvalore (1.234 × 1,50)")).toBeInTheDocument();
  });

  it("explains what is missing or not a number, at the field, on Save", async () => {
    const { calls } = setup();
    await openForm();
    type("Security", WORLD.id);
    type("Date", "");
    type("Quantity", "1,2,3");
    fireEvent.blur(screen.getByLabelText("Quantity"));
    expect(screen.getByLabelText("Quantity")).toHaveAccessibleDescription(
      "Not a number: use digits and one decimal separator.",
    );
    // "Required" waits for Save.
    expect(screen.getByLabelText("Unit price (EUR)")).not.toHaveAttribute("aria-invalid");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByLabelText("Date")).toHaveAccessibleDescription("Fill in this field.");
    const price = screen.getByLabelText("Unit price (EUR)");
    expect(price).toHaveAccessibleDescription("Fill in this field.");
    await waitFor(() => expect(screen.getByLabelText("Date")).toHaveFocus());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows a conflict live, and a refused Save opens the conflict dialog (06)", async () => {
    const conflict = {
      code: "QUANTITY_CONFLICT",
      detail: "x",
      conflict: {
        reason: "NEGATIVE_QUANTITY",
        index: 0,
        account_id: EUR.id,
        asset_id: WORLD.id,
        quantity: "-10",
        held: "50",
      },
    };
    const { calls } = setup({ [PREVIEW]: { status: 409, body: conflict } });
    await openForm();
    fireEvent.click(screen.getByRole("radio", { name: "Sell" }));
    type("Security", WORLD.id);
    type("Date", "2026-10-12");
    type("Quantity", "60");
    type("Unit price (EUR)", "110");
    expect(
      await within(summary()).findByText(
        "Not enough quantity: you would hold 50 before this transaction, and it would leave -10.",
      ),
    ).toBeInTheDocument();

    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    const dialog = await screen.findByRole("dialog", { name: "This change cannot be saved" });
    expect(dialog).toHaveTextContent(
      "It would bring the quantity of Mondo Azionario ETF to -10: " +
        "you cannot sell more than you hold.",
    );
    expect(dialog).toHaveTextContent(
      "First transaction in conflict: Sell of 60 on Oct 12, 2026 (you hold 50).",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Back to the form" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.some((c) => c.path === "/api/v1/transactions")).toBe(false);
  });

  it("puts a field issue of the dry run next to its field (too_many_digits, D-139)", async () => {
    const refused = {
      code: "VALIDATION_FAILED",
      detail: "x",
      transactions: [{ index: 0, issues: [{ field: "gross_amount", code: "too_many_digits" }] }],
    };
    setup({ [PREVIEW]: { status: 422, body: refused } });
    await openForm();
    await fillBuy();
    expect(await within(summary()).findByText("Check the highlighted fields.")).toBeInTheDocument();
    const gross = screen.getByLabelText("Gross amount (EUR)");
    expect(gross).toBeVisible();
    expect(gross).toHaveAccessibleDescription(
      expect.stringContaining("The computed amount has too many digits"),
    );
  });

  it("a deposit has only an amount; a new negative cash is a warning, not a refusal", async () => {
    const { calls } = setup({
      [PREVIEW]: (call) => ({
        body: {
          ...completed((call.body as PreviewIn).transaction!),
          derived: [],
          transaction: (call.body as PreviewIn).transaction,
          cash: { before: "7498.25", change: "-10000.00", after: "-2501.75" },
          position: null,
          warnings: [
            {
              account_id: EUR.id,
              code: "NEGATIVE_CASH",
              date: "2026-10-12",
              transaction_id: null,
              introduced: true,
            },
          ],
        },
      }),
    });
    await openForm();
    fireEvent.click(screen.getByRole("radio", { name: "Withdrawal" }));
    expect(screen.queryByLabelText("Security")).toBeNull();
    expect(screen.queryByLabelText("Quantity")).toBeNull();
    type("Date", "2026-10-12");
    type("Amount (EUR)", "10000");
    const box = summary();
    expect(
      await within(box).findByText("The cash of Conto titoli goes below zero on Oct 12, 2026."),
    ).toBeInTheDocument();
    expect(within(box).getByText("Amount").nextSibling).toHaveTextContent("€10,000.00");
    expect(within(box).getByText("Cash after").nextSibling).toHaveTextContent("-€2,501.75");
    expect(calls.at(-1)?.body).toEqual({
      transaction: {
        account_id: EUR.id,
        type: "WITHDRAWAL",
        trade_date: "2026-10-12",
        total_amount: "10000",
      },
    });
  });

  it("“Save and add another” keeps the type, account, security and date", async () => {
    setup({ [CREATE]: { status: 201, body: { transactions: [], deleted: [], warnings: [] } } });
    const { router } = await openForm();
    await fillBuy();
    await within(summary()).findByText("Total cost");
    fireEvent.click(screen.getByRole("button", { name: "Save and add another" }));
    // The confirmation's live region takes the focus, so it is read first.
    expect((await screen.findByText("Transaction saved.")).parentElement).toHaveFocus();
    expect(router.state.location.pathname).toBe("/transactions/new");
    expect(screen.getByRole("radio", { name: "Buy" })).toBeChecked();
    expect(screen.getByLabelText("Security")).toHaveValue(WORLD.id);
    expect(screen.getByLabelText("Date")).toHaveValue("2026-10-12");
    expect(screen.getByLabelText("Quantity")).toHaveValue("");
    expect(screen.getByLabelText("Fees (EUR)")).toHaveValue("");
  });

  it("asks for the broker's gross when the security is quoted in another currency", async () => {
    setup();
    await openForm();
    type("Security", ROBOTICS.id);
    const gross = screen.getByLabelText("Gross amount (EUR)");
    expect(gross).toHaveAccessibleDescription(
      "The security is quoted in USD: type the amount in EUR from your broker's statement.",
    );
    expect(screen.getByLabelText("Unit price (USD)")).toBeInTheDocument();
  });
});
