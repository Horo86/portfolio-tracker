import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Asset, ManualPrice, PortfolioPositions } from "../api/types";
import { today } from "../format/date";
import { type Handler, stubFetch } from "../test/fetchStub";
import { portfolioRow, WORLD_ETF } from "../test/holdings";
import { HEALTH, ME, renderApp } from "../test/renderApp";

// Screen 12 (D-149): the user's own manual prices of one security. Invented data only.

const ETF: Asset = {
  id: WORLD_ETF.asset_id,
  isin: WORLD_ETF.isin,
  ticker: WORLD_ETF.ticker,
  name: WORLD_ETF.name,
  asset_type: "ETF",
  quote_currency: "EUR",
  quote_unit: "CURRENCY",
  shared: false,
  in_use: true,
  created_at: "2026-01-10T09:00:00Z",
};
const PENCE: Asset = {
  ...ETF,
  id: "00000000-0000-4000-8000-0000000000b7",
  name: "Esempio UK ETF",
  quote_currency: "GBP",
  quote_unit: "MINOR_CURRENCY_UNIT",
};
const BOND: Asset = {
  ...ETF,
  id: "00000000-0000-4000-8000-0000000000b8",
  name: "Esempio BTP 2031",
  isin: null,
  asset_type: "BOND",
  quote_unit: "PERCENT_OF_NOMINAL",
};

const price = (day: string, close: string, n: number): ManualPrice => ({
  id: `00000000-0000-4000-8000-0000000001${String(n).padStart(2, "0")}`,
  day,
  close,
  source: "MANUAL",
});
const OCT_3 = price("2026-10-03", "31.95", 2);
const OCT_1 = price("2026-10-01", "30.8", 1);

const ALL: PortfolioPositions = {
  currency: "EUR",
  day: "2026-10-24",
  partial: false,
  unavailable_currencies: [],
  stale_fx: [],
  positions: [portfolioRow(WORLD_ETF)],
};

const PRICES = `/api/v1/assets/${ETF.id}/prices`;
const HOLDINGS = "GET /api/v1/portfolio/positions?include_closed=true";

function page(extra: Record<string, Handler> = {}, me: object = ME) {
  return stubFetch({
    "GET /api/v1/me": { body: me },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [] },
    "GET /api/v1/assets": { body: [ETF, PENCE, BOND] },
    [HOLDINGS]: { body: ALL },
    [`GET ${PRICES}`]: { body: [OCT_3, OCT_1] },
    ...extra,
  });
}

/** A day as the date field shows it and as it is typed, dd/mm/yyyy. */
const typed = (day: string) => day.split("-").reverse().join("/");

/** The day after the browser's today (YYYY-MM-DD). */
function tomorrow(): string {
  const [year, month, day] = today().split("-").map(Number);
  const next = new Date(year, month - 1, day + 1);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}`;
}

const listRows = () =>
  within(screen.getByRole("table", { name: "Your manual prices" })).getAllByRole("row").slice(1);

describe("manual prices (screen 12, D-149)", () => {
  it("lists only the user's manual prices, newest first, beside the form for a new price", async () => {
    page();
    renderApp(`/holdings/${ETF.id}/prices`);
    expect(await screen.findByRole("heading", { name: "Manual prices", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Esempio Mondo ETF · ETF · EUR")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← Esempio Mondo ETF" })).toHaveAttribute("href", `/holdings/${ETF.id}`);

    // The banner: the status notes and the price in use, from the position.
    expect(await screen.findByText("Price in use: €108.40 on Oct 20, 2026 (automatic)")).toBeInTheDocument();
    expect(screen.getByText("Automatic prices, updated daily.")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "New price" })).toBeInTheDocument();
    expect(screen.getByLabelText("Date")).toHaveValue(typed(today()));
    expect(screen.getByLabelText("Price (EUR)")).toHaveValue("");
    expect(
      screen.getByText(
        "Only for you. It applies to its day and stays in use until a newer price, automatic or manual, exists.",
      ),
    ).toBeInTheDocument();

    expect(await screen.findByText("Automatic prices are not listed here.")).toBeInTheDocument();
    expect(listRows().map((row) => row.textContent)).toEqual([
      expect.stringContaining("Oct 3, 2026€31.95"),
      expect.stringContaining("Oct 1, 2026€30.80"),
    ]);
    expect(screen.queryByText("automatic", { selector: "td *, td" })).toBeNull();
  });

  it("saves a price, then reads the list and the holdings again and says so", async () => {
    let saved = false;
    const { calls } = page({
      [`PUT ${PRICES}`]: () => {
        saved = true;
        return { body: price("2026-10-12", "31.6", 3) };
      },
      [`GET ${PRICES}`]: () => ({ body: saved ? [price("2026-10-12", "31.6", 3), OCT_3, OCT_1] : [OCT_3, OCT_1] }),
    });
    renderApp(`/holdings/${ETF.id}/prices`);
    await screen.findByRole("table", { name: "Your manual prices" });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "12/10/2026" } });
    fireEvent.change(screen.getByLabelText("Price (EUR)"), { target: { value: "31.60" } });
    const holdingsBefore = calls.filter((c) => `GET ${c.path}` === HOLDINGS).length;
    fireEvent.click(screen.getByRole("button", { name: "Save price" }));

    expect(await screen.findByText("Price of Oct 12, 2026 saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ day: "2026-10-12", close: "31.60" });
    await waitFor(() => expect(listRows()).toHaveLength(3));
    expect(calls.filter((c) => `GET ${c.path}` === HOLDINGS).length).toBeGreaterThan(holdingsBefore);
    // The form is ready for the next price.
    expect(screen.getByLabelText("Date")).toHaveValue(typed(today()));
    expect(screen.getByLabelText("Price (EUR)")).toHaveValue("");
  });

  it("refuses a day after today in the page, and shows the server's future_date at the date", async () => {
    const { calls } = page({
      [`PUT ${PRICES}`]: {
        status: 422,
        body: { code: "VALIDATION_FAILED", detail: "x", issues: [{ field: "day", code: "future_date" }] },
      },
    });
    renderApp(`/holdings/${ETF.id}/prices`);
    const date = await screen.findByLabelText("Date");
    // The browser's calendar stops at today; a later day typed is refused by the page.
    expect(document.querySelector('input[type="date"]')).toHaveAttribute("max", today());
    fireEvent.change(date, { target: { value: typed(tomorrow()) } });
    fireEvent.change(screen.getByLabelText("Price (EUR)"), { target: { value: "31" } });
    fireEvent.click(screen.getByRole("button", { name: "Save price" }));
    expect(await screen.findByText("The date cannot be in the future.")).toBeInTheDocument();
    expect(date).toHaveAttribute("aria-invalid", "true");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);

    // The server's clock is behind the browser's: its refusal shows at the same field.
    fireEvent.change(date, { target: { value: typed(today()) } });
    expect(screen.queryByText("The date cannot be in the future.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save price" }));
    expect(await screen.findByText("The date cannot be in the future.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);
  });

  it("checks the price as typed: required, a number, not negative", async () => {
    const { calls } = page();
    renderApp(`/holdings/${ETF.id}/prices`);
    const field = await screen.findByLabelText("Price (EUR)");
    fireEvent.click(screen.getByRole("button", { name: "Save price" }));
    expect(await screen.findByText("Fill in this field.")).toBeInTheDocument();
    expect(field).toHaveFocus();
    fireEvent.change(field, { target: { value: "abc" } });
    expect(screen.getByText("Not a number: use digits and one decimal separator.")).toBeInTheDocument();
    fireEvent.change(field, { target: { value: "-1" } });
    expect(screen.getByText("The price cannot be negative.")).toBeInTheDocument();
    fireEvent.change(field, { target: { value: "0" } }); // zero is allowed
    expect(field).not.toHaveAttribute("aria-invalid");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  it("says in Italian that a price cannot be negative", async () => {
    page({}, { ...ME, language: "it" });
    renderApp(`/holdings/${ETF.id}/prices`);
    fireEvent.change(await screen.findByLabelText("Prezzo (EUR)"), { target: { value: "-1" } });
    expect(screen.getByText("Il prezzo non può essere negativo.")).toBeInTheDocument();
    expect(screen.getByText("Prezzo in uso: 108,40 € del 20 ott 2026 (automatico)")).toBeInTheDocument();
  });

  it("warns before saving over a day that already has a manual price", async () => {
    page();
    renderApp(`/holdings/${ETF.id}/prices`);
    await screen.findByRole("table", { name: "Your manual prices" });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "01/10/2026" } });
    expect(
      screen.getByText("You already have a manual price for Oct 1, 2026: it will be replaced."),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "02/10/2026" } });
    expect(screen.queryByText(/it will be replaced/)).toBeNull();
  });

  it("edits a price with its day fixed; Cancel goes back to a new price", async () => {
    const { calls } = page({ [`PUT ${PRICES}`]: { body: { ...OCT_1, close: "30.9" } } });
    renderApp(`/holdings/${ETF.id}/prices`);
    fireEvent.click(await screen.findByRole("button", { name: "Edit the price of Oct 1, 2026" }));
    expect(screen.getByRole("heading", { name: "Edit the price of Oct 1, 2026" })).toBeInTheDocument();
    const date = screen.getByLabelText("Date");
    expect(date).toHaveValue("01/10/2026");
    expect(date).toHaveAttribute("readonly");
    const field = screen.getByLabelText("Price (EUR)");
    expect(field).toHaveValue("30.8");
    expect(field).toHaveFocus();
    expect(screen.queryByText(/it will be replaced/)).toBeNull(); // editing that very day

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("heading", { name: "New price" })).toBeInTheDocument();
    expect(screen.getByLabelText("Date")).not.toHaveAttribute("readonly");
    expect(screen.getByLabelText("Date")).toHaveValue(typed(today()));

    fireEvent.click(screen.getByRole("button", { name: "Edit the price of Oct 1, 2026" }));
    fireEvent.change(screen.getByLabelText("Price (EUR)"), { target: { value: "30.9" } });
    fireEvent.click(screen.getByRole("button", { name: "Save price" }));
    expect(await screen.findByText("Price of Oct 1, 2026 saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ day: "2026-10-01", close: "30.9" });
    expect(screen.getByRole("heading", { name: "New price" })).toBeInTheDocument();
  });

  it("deletes a price after asking; one already gone is said and the list read again", async () => {
    let gone = false;
    const { calls } = page({
      [`DELETE ${PRICES}/${OCT_1.id}`]: { status: 204 },
      [`DELETE ${PRICES}/${OCT_3.id}`]: { status: 404, body: { code: "NOT_FOUND", detail: "x" } },
      [`GET ${PRICES}`]: () => ({ body: gone ? [] : [OCT_3, OCT_1] }),
    });
    renderApp(`/holdings/${ETF.id}/prices`);
    fireEvent.click(await screen.findByRole("button", { name: "Delete the price of Oct 1, 2026" }));
    const dialog = screen.getByRole("dialog", { name: "Delete the price of Oct 1, 2026?" });
    expect(dialog).toHaveTextContent(
      "The security will be valued at the most recent earlier price, or at cost if there is none.",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Delete the price of Oct 1, 2026" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete price" }));
    expect(await screen.findByText("Price of Oct 1, 2026 deleted.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();

    gone = true;
    fireEvent.click(screen.getByRole("button", { name: "Delete the price of Oct 3, 2026" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete price" }));
    expect(await screen.findByText("This price no longer exists.")).toBeInTheDocument();
    expect(await screen.findByText("No manual prices for this security yet.")).toBeInTheDocument();
  });

  it("deleting the price being edited goes back to a new price", async () => {
    page({ [`DELETE ${PRICES}/${OCT_1.id}`]: { status: 204 } });
    renderApp(`/holdings/${ETF.id}/prices`);
    fireEvent.click(await screen.findByRole("button", { name: "Edit the price of Oct 1, 2026" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete the price of Oct 1, 2026" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete price" }));
    expect(await screen.findByText("Price of Oct 1, 2026 deleted.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "New price" })).toBeInTheDocument();
  });

  it("deletes once for two clicks in the same moment", async () => {
    const { calls } = page({ [`DELETE ${PRICES}/${OCT_1.id}`]: { status: 204 } });
    renderApp(`/holdings/${ETF.id}/prices`);
    fireEvent.click(await screen.findByRole("button", { name: "Delete the price of Oct 1, 2026" }));
    const button = within(screen.getByRole("dialog")).getByRole("button", { name: "Delete price" });
    const click = () => button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    click();
    click();
    expect(await screen.findByText("Price of Oct 1, 2026 deleted.")).toBeInTheDocument();
    expect(screen.queryByText("This price no longer exists.")).toBeNull();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });

  it("edits a price dated after the browser's today (the server allows tomorrow)", async () => {
    const ahead = price(tomorrow(), "31", 9);
    const { calls } = page({
      [`GET ${PRICES}`]: { body: [ahead, OCT_1] },
      [`PUT ${PRICES}`]: { body: { ...ahead, close: "32" } },
    });
    renderApp(`/holdings/${ETF.id}/prices`);
    // Newest first: the row dated tomorrow.
    fireEvent.click((await screen.findAllByRole("button", { name: /^Edit the price of/ }))[0]);
    fireEvent.change(screen.getByLabelText("Price (EUR)"), { target: { value: "32" } });
    fireEvent.click(screen.getByRole("button", { name: "Save price" }));
    expect(await screen.findByText(/saved\.$/)).toBeInTheDocument();
    expect(screen.queryByText("The date cannot be in the future.")).toBeNull();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ day: tomorrow(), close: "32" });
  });

  it("keeps the edited price's day whatever reaches the read-only date field", async () => {
    const { calls } = page({ [`PUT ${PRICES}`]: { body: OCT_1 } });
    renderApp(`/holdings/${ETF.id}/prices`);
    fireEvent.click(await screen.findByRole("button", { name: "Edit the price of Oct 1, 2026" }));
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-09-15" } });
    expect(screen.getByLabelText("Date")).toHaveValue("01/10/2026");
    fireEvent.click(screen.getByRole("button", { name: "Save price" }));
    expect(await screen.findByText("Price of Oct 1, 2026 saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ day: "2026-10-01", close: "30.8" });
  });

  it("offers no edit or delete of a row while a save is on its way", async () => {
    let release = () => {};
    const answer = new Promise<{ body: ManualPrice }>((resolve) => {
      release = () => resolve({ body: OCT_1 });
    });
    page({ [`PUT ${PRICES}`]: () => answer });
    renderApp(`/holdings/${ETF.id}/prices`);
    fireEvent.click(await screen.findByRole("button", { name: "Edit the price of Oct 1, 2026" }));
    fireEvent.click(screen.getByRole("button", { name: "Save price" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save price" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Delete the price of Oct 1, 2026" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit the price of Oct 3, 2026" })).toBeDisabled();
    release();
    expect(await screen.findByText("Price of Oct 1, 2026 saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete the price of Oct 1, 2026" })).toBeEnabled();
  });

  it("keeps the date as it is while a save is on its way (M5 slice 6)", async () => {
    let release = () => {};
    const answer = new Promise<{ body: ManualPrice }>((resolve) => {
      release = () => resolve({ body: { ...OCT_1, day: "2026-10-12" } });
    });
    const { calls } = page({ [`PUT ${PRICES}`]: () => answer });
    renderApp(`/holdings/${ETF.id}/prices`);
    await screen.findByRole("table", { name: "Your manual prices" });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "12/10/2026" } });
    fireEvent.change(screen.getByLabelText("Price (EUR)"), { target: { value: "31" } });
    fireEvent.click(screen.getByRole("button", { name: "Save price" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save price" })).toBeDisabled());
    expect(screen.getByLabelText("Date")).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Pick from the calendar: Date" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "11/10/2026" } });
    expect(screen.getByLabelText("Date")).toHaveValue("12/10/2026");
    release();
    expect(await screen.findByText("Price of Oct 12, 2026 saved.")).toBeInTheDocument();
    expect(screen.getByLabelText("Date")).not.toHaveAttribute("readonly");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);
  });

  it("takes the banner from all accounts when the account in view does not hold the security", async () => {
    const account = { id: "00000000-0000-4000-8000-0000000000a1", name: "Conto vuoto", provider: null,
                      currency: "EUR", external_code: null, created_at: "2026-01-10T09:00:00Z",
                      transaction_count: 0, currency_locked: false }; // prettier-ignore
    page({
      "GET /api/v1/accounts": { body: [account] },
      [`GET /api/v1/accounts/${account.id}/positions?include_closed=true`]: { body: [] },
    });
    renderApp(`/holdings/${ETF.id}/prices?account=${account.id}`);
    expect(await screen.findByText("Price in use: €108.40 on Oct 20, 2026 (automatic)")).toBeInTheDocument();
  });

  it("after a delete the focus goes to the confirmation; after show all, to the list", async () => {
    let gone = false;
    const many = Array.from({ length: 21 }, (_, i) =>
      price(`2026-09-${String(30 - i).padStart(2, "0")}`, "10", i),
    );
    page({
      [`DELETE ${PRICES}/${many[0].id}`]: () => {
        gone = true;
        return { status: 204 };
      },
      [`GET ${PRICES}`]: () => ({ body: gone ? many.slice(1) : many }),
    });
    renderApp(`/holdings/${ETF.id}/prices`);
    await screen.findByRole("table", { name: "Your manual prices" });
    fireEvent.click(screen.getByRole("button", { name: "Show all (21)" }));
    expect(screen.getByRole("table", { name: "Your manual prices" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Delete the price of Sep 30, 2026" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete price" }));
    const notice = await screen.findByText("Price of Sep 30, 2026 deleted.");
    await waitFor(() => expect(notice.closest("[role=status]")).toHaveFocus());
  });

  it("shows the latest 20, then all of them on request", async () => {
    const many = Array.from({ length: 23 }, (_, i) =>
      price(`2026-09-${String(30 - i).padStart(2, "0")}`, "10", i),
    );
    page({ [`GET ${PRICES}`]: { body: many } });
    renderApp(`/holdings/${ETF.id}/prices`);
    await screen.findByRole("table", { name: "Your manual prices" });
    expect(listRows()).toHaveLength(20);
    fireEvent.click(screen.getByRole("button", { name: "Show all (23)" }));
    expect(listRows()).toHaveLength(23);
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
  });

  it("saves once for two clicks in the same moment", async () => {
    const { calls } = page({ [`PUT ${PRICES}`]: { body: price("2026-10-12", "1", 3) } });
    renderApp(`/holdings/${ETF.id}/prices`);
    await screen.findByRole("table", { name: "Your manual prices" });
    fireEvent.change(screen.getByLabelText("Price (EUR)"), { target: { value: "1" } });
    const button = screen.getByRole("button", { name: "Save price" });
    // Two clicks in one task, with no render between them (outside act).
    const click = () => button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    click();
    click();
    expect(await screen.findByText(/saved\.$/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);
  });

  it("asks for the price in the security's unit: hundredths, or % of nominal", async () => {
    page({
      [`GET /api/v1/assets/${PENCE.id}/prices`]: { body: [price("2026-10-01", "8675", 4)] },
      [`GET /api/v1/assets/${BOND.id}/prices`]: { body: [] },
    });
    const { router } = renderApp(`/holdings/${PENCE.id}/prices`);
    expect(await screen.findByLabelText("Price (hundredths of GBP)")).toBeInTheDocument();
    expect(await screen.findByText("8,675.00 hundredths of GBP")).toBeInTheDocument();
    await router.navigate(`/holdings/${BOND.id}/prices`);
    expect(await screen.findByLabelText("Price (% of nominal)")).toBeInTheDocument();
    expect(await screen.findByText("No manual prices for this security yet.")).toBeInTheDocument();
  });

  it("with market data off the page works the same and the banner says prices are off", async () => {
    const off = { ...WORLD_ETF, price: null, price_date: null, price_source: null, price_exchange: null,
                  price_status: "PENDING" as const, estimated_at_cost: true }; // prettier-ignore
    page({ [HOLDINGS]: { body: { ...ALL, positions: [portfolioRow(off)] } } }, { ...ME, market_data: "OFF" });
    renderApp(`/holdings/${ETF.id}/prices`);
    expect(
      await screen.findByText("Automatic prices are off on this installation: enter prices by hand."),
    ).toBeInTheDocument();
    expect(screen.getByText("Estimated at cost")).toBeInTheDocument();
    expect(screen.queryByText(/Price in use/)).toBeNull();
    expect(screen.getByRole("button", { name: "Save price" })).toBeEnabled();
  });

  it("an unknown security says so", async () => {
    page();
    renderApp("/holdings/00000000-0000-4000-8000-0000000000ff/prices");
    expect(await screen.findByText("This security does not exist or was deleted.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save price" })).toBeNull();
  });
});
