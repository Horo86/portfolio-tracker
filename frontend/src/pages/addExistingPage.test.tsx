import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, Asset, BatchIn, BatchPreview, TransactionFields } from "../api/types";
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
const OTHER: Account = { ...EUR, id: "00000000-0000-4000-8000-0000000000a2", name: "Altro conto" };
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
const BTP: Asset = {
  ...WORLD,
  id: "00000000-0000-4000-8000-0000000000b3",
  name: "BTP Esempio 2031",
  asset_type: "BOND",
  quote_unit: "PERCENT_OF_NOMINAL",
};
const NEW: Asset = { ...WORLD, id: "00000000-0000-4000-8000-0000000000b9", name: "Esempio Nuovo ETF" };

const PREVIEW = "POST /api/v1/transactions/batch/preview";
const SAVE = "POST /api/v1/transactions/batch";

// The server's completion: the gross is quantity × price unless typed; no fees.
const complete = (tx: TransactionFields): TransactionFields => {
  const gross = tx.gross_amount ?? (tx.quantity === "19" ? "1938.00" : "500.00");
  return { ...tx, gross_amount: gross, total_amount: gross };
};
const echo: Handler = (call: Call) => {
  const { transactions } = call.body as BatchIn;
  const answer: BatchPreview = {
    transactions: transactions.map(complete),
    derived: transactions.map(() => ["gross_amount", "total_amount"]),
    warnings: [],
  };
  return { body: answer };
};

function setup(routes: Record<string, Handler> = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: ME },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [EUR, OTHER] },
    "GET /api/v1/assets": { body: [WORLD, ROBOTICS, BTP] },
    "GET /api/v1/currencies": { body: ["EUR", "USD"] },
    [`GET /api/v1/accounts/${EUR.id}/positions?include_closed=true`]: { body: [] },
    [PREVIEW]: echo,
    ...routes,
  });
}

async function open(path = `/holdings/existing?account=${EUR.id}`) {
  const result = renderApp(path);
  await screen.findByRole("heading", { name: "Add holdings I already own", level: 1 });
  await screen.findAllByRole("option", { name: "Mondo Azionario ETF · EUR" });
  return result;
}

const field = (name: string, row: number) => screen.getByLabelText(`${name}, row ${row}`);
const type = (name: string, row: number, value: string) =>
  fireEvent.change(field(name, row), { target: { value } });
const fillRow = (row: number, asset: Asset, quantity: string, price: string) => {
  type("Security", row, asset.id);
  type(asset.asset_type === "BOND" ? "Nominal" : "Quantity", row, quantity);
  type("Average cost", row, price);
};
const save = () => fireEvent.click(screen.getByRole("button", { name: /^Save \d+ securit/ }));
const posts = (calls: Call[], path: string) =>
  calls.filter((c) => c.method === "POST" && c.path === path);

describe("add holdings I already own (10)", () => {
  it("starts with the account in view, today, three empty rows and the security asked for", async () => {
    setup();
    await open(`/holdings/existing?account=${EUR.id}&asset=${WORLD.id}`);
    expect(screen.getByLabelText("Account")).toHaveValue(EUR.id);
    expect(screen.getByLabelText("Held on")).toHaveValue(new Date().toLocaleDateString("sv"));
    expect(screen.getAllByRole("row")).toHaveLength(4); // the header and three rows
    expect(field("Security", 1)).toHaveValue(WORLD.id);
    expect(field("Security", 2)).toHaveValue("");
    expect(screen.getByText("0 securities ready · Each row becomes a purchase that does not move cash.")).toBeInTheDocument();
  });

  it("leaves out a security asked for that the user does not have", async () => {
    const { calls } = setup();
    await open(`/holdings/existing?account=${EUR.id}&asset=00000000-0000-4000-8000-0000000000ff`);
    expect(field("Security", 1)).toHaveValue("");
    save();
    expect(await screen.findByText("Fill in at least one row.")).toBeInTheDocument();
    expect(screen.queryByText("Complete the row or clear it.")).not.toBeInTheDocument();
    expect(posts(calls, "/api/v1/transactions/batch/preview")).toHaveLength(0);
  });

  it("coming from a conflict, asks for a date before the sale", async () => {
    setup();
    await open(`/holdings/existing?account=${EUR.id}&asset=${WORLD.id}&before=2026-10-12`);
    expect(
      screen.getByText("To cover the sale that is in conflict, choose a date before Oct 12, 2026."),
    ).toBeInTheDocument();
  });

  it("with all accounts in view asks for one before saving, and sends nothing", async () => {
    const { calls } = setup();
    await open("/holdings/existing");
    expect(screen.getByLabelText("Account")).toHaveValue("");
    fillRow(1, WORLD, "19", "102");
    // The row is complete: it counts as ready before an account is chosen.
    expect(screen.getByText(/^1 security ready/)).toBeInTheDocument();
    save();
    expect(await screen.findByText("Fill in this field.")).toBeInTheDocument();
    expect(screen.getByLabelText("Account")).toHaveFocus();
    expect(screen.queryByText("Fill in at least one row.")).not.toBeInTheDocument();
    expect(posts(calls, "/api/v1/transactions/batch")).toHaveLength(0);
  });

  it("shows each row's total from the dry run and saves exactly the rows it completed", async () => {
    const { calls, fetchMock } = setup({
      [SAVE]: (call) => ({
        status: 201,
        body: { transactions: (call.body as BatchIn).transactions.map((tx, i) => ({ ...tx, id: `t${i}` })), deleted: [], warnings: [] },
      }),
    });
    const { router } = await open();
    fillRow(1, WORLD, "19", "102");
    fillRow(3, BTP, "5000", "98.5");
    expect(field("Nominal", 3)).toHaveAccessibleDescription("in EUR");
    expect(field("Average cost", 3)).toHaveAccessibleDescription("% of nominal");
    const rows = screen.getAllByRole("row");
    await waitFor(() => expect(rows[1]).toHaveTextContent("€1,938.00"));
    expect(rows[3]).toHaveTextContent("€500.00");
    expect(rows[2]).toHaveTextContent("—");
    expect(screen.getByText(/^2 securities ready/)).toBeInTheDocument();

    const before = fetchMock.mock.calls.length;
    save();
    await waitFor(() => expect(router.state.location.pathname).toBe("/holdings"));
    expect(router.state.location.search).toBe(`?account=${EUR.id}`);
    expect(await screen.findByText("2 securities added to “Conto titoli”.")).toBeInTheDocument();
    // A fresh dry run first, then the batch with exactly its completed rows.
    const after = calls.slice(before).filter((c) => c.method === "POST");
    expect(after.map((c) => c.path)).toEqual([
      "/api/v1/transactions/batch/preview",
      "/api/v1/transactions/batch",
    ]);
    const sent = (after[1].body as BatchIn).transactions;
    expect(sent).toEqual((after[0].body as BatchIn).transactions.map(complete));
    expect(sent.map((tx) => [tx.type, tx.affects_cash, tx.asset_id, tx.quantity, tx.unit_price])).toEqual([
      ["BUY", false, WORLD.id, "19", "102"],
      ["BUY", false, BTP.id, "5000", "98.5"],
    ]);
  });

  it("says a future date once, not once per row, and in the saved notice too", async () => {
    const future = (tx: TransactionFields, i: number) => ({
      account_id: EUR.id,
      code: "FUTURE_DATE",
      date: tx.trade_date,
      transaction_id: null,
      introduced: true,
      index: i,
    });
    setup({
      [PREVIEW]: (call) => {
        const { transactions } = call.body as BatchIn;
        return {
          body: {
            transactions: transactions.map(complete),
            derived: transactions.map(() => ["gross_amount", "total_amount"]),
            warnings: transactions.map(future),
          },
        };
      },
      [SAVE]: (call) => {
        const { transactions } = call.body as BatchIn;
        return {
          status: 201,
          body: {
            transactions: transactions.map((tx, i) => ({ ...tx, id: `t${i}` })),
            deleted: [],
            warnings: transactions.map((tx, i) => ({ ...future(tx, i), transaction_id: `t${i}` })),
          },
        };
      },
    });
    await open();
    fireEvent.change(screen.getByLabelText("Held on"), { target: { value: "2027-01-05" } });
    fillRow(1, WORLD, "19", "102");
    fillRow(2, WORLD, "5", "100");
    expect(await screen.findByText("The date Jan 5, 2027 is in the future.")).toBeInTheDocument();
    expect(screen.getAllByText("The date Jan 5, 2027 is in the future.")).toHaveLength(1);
    save();
    expect(
      await screen.findByText(
        "2 securities added to “Conto titoli”. The date Jan 5, 2027 is in the future.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the warnings in place while the next dry run is on its way", async () => {
    let hold: (() => void) | null = null;
    let first = true;
    setup({
      [PREVIEW]: (call) => {
        const { transactions } = call.body as BatchIn;
        const body = {
          transactions: transactions.map(complete),
          derived: transactions.map(() => ["gross_amount", "total_amount"]),
          warnings: transactions.map((tx, index) => ({
            account_id: EUR.id, code: "FUTURE_DATE", date: tx.trade_date,
            transaction_id: null, introduced: true, index,
          })),
        }; // prettier-ignore
        if (first) {
          first = false;
          return { body };
        }
        return new Promise((resolve) => {
          hold = () => resolve({ body });
        });
      },
    });
    await open();
    fireEvent.change(screen.getByLabelText("Held on"), { target: { value: "2027-01-05" } });
    fillRow(1, WORLD, "19", "102");
    const text = "The date Jan 5, 2027 is in the future.";
    expect(await screen.findByText(text)).toBeInTheDocument();
    type("Quantity", 1, "20");
    await waitFor(() => expect(hold).not.toBeNull());
    expect(screen.getByText(text)).toBeInTheDocument();
    hold!();
  });

  it("a quick second Save after a refused one sends the batch once", async () => {
    let refuse = true;
    const { calls } = setup({
      [SAVE]: (call) =>
        refuse
          ? { status: 409, body: { code: "CONFLICT" } }
          : {
              status: 201,
              body: { transactions: (call.body as BatchIn).transactions, deleted: [], warnings: [] },
            },
    });
    await open();
    fillRow(1, WORLD, "19", "102");
    save();
    expect(await screen.findByText(/The data changed meanwhile/)).toBeInTheDocument();
    refuse = false;
    type("Average cost", 1, "103");
    const before = posts(calls, "/api/v1/transactions/batch").length;
    const button = screen.getByRole("button", { name: /^Save 1 security/ });
    // Two clicks in one task, with no render between them (outside act).
    const click = () => button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    click();
    click();
    await screen.findByText("1 security added to “Conto titoli”.");
    expect(posts(calls, "/api/v1/transactions/batch").length - before).toBe(1);
  });

  it("a half-filled row blocks Save", async () => {
    const { calls } = setup();
    await open();
    fillRow(1, WORLD, "19", "102");
    type("Quantity", 2, "7");
    save();
    const errors = await screen.findAllByText("Complete the row or clear it.");
    expect(errors).toHaveLength(2); // its security and its average cost
    expect(field("Security", 2)).toHaveFocus();
    expect(posts(calls, "/api/v1/transactions/batch")).toHaveLength(0);
    // Cleared, it is ignored again.
    type("Quantity", 2, "");
    await waitFor(() => expect(screen.queryByText("Complete the row or clear it.")).not.toBeInTheDocument());
  });

  it("asks a row in another currency for its total in the account currency", async () => {
    const { calls } = setup();
    await open();
    fillRow(1, ROBOTICS, "30", "94.10");
    // Each said once: the label names the field and the row, the hint its currency.
    expect(field("Total cost", 1)).toHaveAccessibleDescription("in EUR");
    expect(screen.getByText("Priced in USD: type the total cost in EUR as the broker charged it.")).toBeInTheDocument();
    type("Total cost", 1, "2,595.00");
    await waitFor(() => expect(posts(calls, "/api/v1/transactions/batch/preview")).toHaveLength(1));
    expect((posts(calls, "/api/v1/transactions/batch/preview")[0].body as BatchIn).transactions[0])
      .toMatchObject({ asset_id: ROBOTICS.id, quantity: "30", unit_price: "94.10", gross_amount: "2595.00" });
  });

  it("allows the same security twice, with a hint", async () => {
    setup();
    await open();
    // The page's words for the account's cash are those of the rest of the app (cassa).
    await i18n.changeLanguage("it");
    expect(await screen.findByText(/La cassa del conto non cambia\./)).toBeInTheDocument();
    expect(screen.getByText(/acquisto che non muove la cassa/)).toBeInTheDocument();
    await i18n.changeLanguage("en");
    type("Security", 1, WORLD.id);
    type("Security", 3, WORLD.id);
    expect(field("Security", 3)).toHaveAccessibleDescription("The same security is also in row 1.");
    expect(field("Security", 1)).toHaveAccessibleDescription("The same security is also in row 3.");
  });

  it("says when no row is filled in", async () => {
    const { calls } = setup();
    await open();
    save();
    expect(await screen.findByText("Fill in at least one row.")).toBeInTheDocument();
    expect(posts(calls, "/api/v1/transactions/batch/preview")).toHaveLength(0);
  });

  it("adds rows up to 50 and removes them", async () => {
    setup();
    await open();
    const add = screen.getByRole("button", { name: "+ Add row" });
    for (let i = 0; i < 47; i += 1) fireEvent.click(add);
    expect(screen.getAllByRole("row")).toHaveLength(51);
    expect(add).toBeDisabled();
    expect(screen.getByText("At most 50 rows at a time.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove row 50" }));
    expect(screen.getAllByRole("row")).toHaveLength(50);
    expect(add).toBeEnabled();
  });

  it("puts a 422 on its row and stores nothing", async () => {
    setup({
      [SAVE]: {
        status: 422,
        body: {
          code: "VALIDATION_FAILED",
          transactions: [{ index: 1, issues: [{ field: "quantity", code: "too_many_decimals" }] }],
        },
      },
    });
    await open();
    fillRow(1, WORLD, "19", "102");
    fillRow(3, WORLD, "5", "100");
    await waitFor(() => expect(screen.getAllByRole("row")[3]).toHaveTextContent("€500.00"));
    save();
    // Index 1 of the request is the third row on screen (the second is empty).
    await waitFor(() =>
      expect(field("Quantity", 3)).toHaveAccessibleDescription(/at most 8 decimals/i),
    );
    expect(field("Quantity", 3)).toHaveFocus();
    expect(field("Quantity", 1)).not.toHaveAttribute("aria-invalid");
  });

  it("an issue on a row's computed total takes the focus and is announced", async () => {
    setup({
      [PREVIEW]: echo,
      [SAVE]: {
        status: 422,
        body: {
          code: "VALIDATION_FAILED",
          transactions: [{ index: 0, issues: [{ field: "gross_amount", code: "too_many_digits" }] }],
        },
      },
    });
    await open();
    fillRow(2, WORLD, "19", "102");
    await waitFor(() => expect(screen.getAllByRole("row")[2]).toHaveTextContent("€1,938.00"));
    save();
    const price = field("Average cost", 2);
    await waitFor(() => expect(price).toHaveFocus());
    expect(price).toHaveAccessibleDescription(/too many digits/);
    expect(screen.getByText("Check row 2.").closest("[role=alert]")).not.toBeNull();
  });

  it("names a conflict by its row, or above when it is a stored transaction", async () => {
    const conflict = (where: object) => ({
      status: 409,
      body: {
        code: "QUANTITY_CONFLICT",
        conflict: { reason: "NEGATIVE_QUANTITY", account_id: EUR.id, asset_id: WORLD.id, quantity: "-1", held: "0", ...where },
      },
    });
    let reply = conflict({ index: 0 });
    setup({ [SAVE]: () => reply });
    await open();
    fillRow(2, WORLD, "19", "102");
    save();
    const rowTwo = () => screen.getAllByRole("row")[2];
    await waitFor(() =>
      expect(rowTwo()).toHaveTextContent("The change was refused: a quantity would become negative."),
    );

    reply = conflict({ transaction_id: "00000000-0000-4000-8000-0000000000c1" });
    type("Average cost", 2, "103");
    save();
    const alert = await screen.findByText("The change was refused: a quantity would become negative.", {
      selector: "[role=alert]",
    });
    expect(alert).toBeInTheDocument();
    expect(rowTwo()).not.toHaveTextContent("quantity would become negative");
  });

  it("“New security…” searches or creates in a dialog and puts it in that row", async () => {
    let created = false;
    setup({
      "GET /api/v1/assets": () => ({ body: created ? [WORLD, ROBOTICS, BTP, NEW] : [WORLD, ROBOTICS, BTP] }),
      "POST /api/v1/assets": () => {
        created = true;
        return { status: 201, body: NEW };
      },
    });
    await open();
    type("Quantity", 1, "4");
    fireEvent.click(screen.getByRole("button", { name: "New security for row 2" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a security" });
    expect(within(dialog).getByLabelText("Search by ISIN, name or symbol")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: NEW.name } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create security" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(field("Security", 2)).toHaveValue(NEW.id);
    expect(field("Quantity", 1)).toHaveValue("4"); // the rows stay
    expect(screen.getByText("“Esempio Nuovo ETF” added.")).toBeInTheDocument();
  });

  it("offers the user's own security when the ISIN is taken, for that row", async () => {
    const mine = { ...WORLD, isin: "ZZ0000087013" };
    setup({
      "GET /api/v1/assets": { body: [mine, ROBOTICS, BTP] },
      "POST /api/v1/assets": { status: 409, body: { code: "ISIN_ALREADY_USED" } },
    });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "New security for row 1" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a security" });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Doppio" } });
    fireEvent.change(within(dialog).getByLabelText("ISIN (optional)"), { target: { value: mine.isin } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create security" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "Choose this security" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(field("Security", 1)).toHaveValue(mine.id);
  });
});
