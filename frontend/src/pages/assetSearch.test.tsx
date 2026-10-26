import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Account, Asset, SearchResult } from "../api/types";
import { type Handler, stubFetch } from "../test/fetchStub";
import { HEALTH, ME, renderApp } from "../test/renderApp";

// Invented ISINs, names, symbols and accounts only.
const ISIN = "ZZ0000087013";
const EUR_ACCOUNT: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto titoli",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 3,
  currency_locked: true,
};
const USD_ACCOUNT: Account = {
  ...EUR_ACCOUNT,
  id: "00000000-0000-4000-8000-0000000000a2",
  name: "Conto estero",
  currency: "USD",
};
const listing = (extra: Partial<SearchResult> = {}): SearchResult => ({
  name: "Esempio Mondo ETF",
  symbol: "ZWLD.MI",
  exchange: "Milan",
  currency: "EUR",
  quote_unit: "CURRENCY",
  asset_type: "ETF",
  isin: ISIN,
  ...extra,
});
const WORLD = [
  listing(),
  listing({ symbol: "ZWLD.DE", exchange: "XETRA" }),
  listing({ symbol: "ZWLD.L", exchange: "London", currency: "GBP", quote_unit: "MINOR_CURRENCY_UNIT" }),
];
const CREATED: Asset = {
  id: "00000000-0000-4000-8000-0000000000b9",
  isin: ISIN,
  ticker: null,
  name: "Esempio Mondo ETF",
  asset_type: "ETF",
  quote_currency: "EUR",
  quote_unit: "CURRENCY",
  shared: false,
  in_use: false,
  created_at: "2026-10-20T09:00:00Z",
};
const SEARCH = "POST /api/v1/assets/search";
const CREATE = "POST /api/v1/assets";

function setup(routes: Record<string, Handler> = {}, me: Record<string, unknown> = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: { ...ME, ...me } },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": { body: [EUR_ACCOUNT, USD_ACCOUNT] },
    "GET /api/v1/currencies": { body: ["EUR", "GBP", "USD"] },
    ...routes,
  });
}

async function open(path = "/holdings/add") {
  const result = renderApp(path);
  await screen.findByRole("heading", { name: "Add a security", level: 1 });
  return result;
}

const searchFor = (text: string) => {
  fireEvent.change(screen.getByLabelText("Search by ISIN, name or symbol"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
};

const posts = (calls: { method: string; path: string }[], path: string) =>
  calls.filter((c) => c.method === "POST" && c.path === path);

describe("search page (11)", () => {
  it("with market data off says so, sends no search and still creates by hand", async () => {
    const { calls } = setup({ [CREATE]: { status: 201, body: { ...CREATED, isin: null } } }, {
      market_data: "OFF",
    });
    await open();
    expect(
      screen.getByText(/Automatic prices are off on this installation: the search is not available/),
    ).toBeInTheDocument();
    const box = screen.getByLabelText("Search by ISIN, name or symbol");
    expect(box).toBeDisabled();
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("search"));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(posts(calls, "/api/v1/assets/search")).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Esempio Fondo Locale" } });
    fireEvent.click(screen.getByRole("button", { name: "Create security" }));
    expect(await screen.findByText(/No ISIN: you enter its prices yourself\./)).toBeInTheDocument();
  });

  it("sends a search only on the button or Enter, and checks its length first", async () => {
    const { calls } = setup({ [SEARCH]: { body: WORLD } });
    await open();
    const box = screen.getByLabelText("Search by ISIN, name or symbol");
    fireEvent.change(box, { target: { value: "Z" } });
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(posts(calls, "/api/v1/assets/search")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Type 2 to 40 characters.")).toBeInTheDocument();
    expect(posts(calls, "/api/v1/assets/search")).toHaveLength(0);

    fireEvent.change(box, { target: { value: ` ${ISIN} ` } });
    fireEvent.submit(screen.getByRole("search"));
    expect(await screen.findByText("2 results")).toBeInTheDocument();
    expect(posts(calls, "/api/v1/assets/search").map((c) => (c as { body?: unknown }).body)).toEqual([
      { q: ISIN },
    ]);
  });

  it("hides the results of the last search while the box asks for 2 to 40 characters", async () => {
    setup({ [SEARCH]: { body: [WORLD[2]] } });
    await open();
    searchFor(ISIN);
    expect(await screen.findByText("1 result")).toBeInTheDocument();
    searchFor("Z");
    expect(await screen.findByText("Type 2 to 40 characters.")).toBeInTheDocument();
    expect(screen.queryByText("1 result")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Add “/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Not in your currency\?/)).not.toBeInTheDocument();
  });

  it("shows one row per ISIN and currency with its exchanges, and no badge", async () => {
    setup({ [SEARCH]: { body: WORLD } });
    await open();
    searchFor(ISIN);
    const add = await screen.findByRole("button", { name: "Add “Esempio Mondo ETF” (EUR)" });
    const row = add.closest("li")!;
    expect(row).toHaveTextContent(`ETF · ${ISIN} · EUR`);
    expect(row).toHaveTextContent("Exchanges: Milan, XETRA");
    expect(row).not.toHaveTextContent(/automatic prices/i);
    expect(screen.getByRole("button", { name: "Add “Esempio Mondo ETF” (GBP)" }).closest("li"))
      .toHaveTextContent("Exchanges: London");
    // Your currency (euro) is among the results: no offer.
    expect(screen.queryByText(/Not in your currency\?/)).not.toBeInTheDocument();
  });

  it("“Add” fills the form in and stores nothing until “Create security”", async () => {
    const { calls } = setup({
      [SEARCH]: { body: WORLD },
      [CREATE]: { status: 201, body: { ...CREATED, name: "Esempio Mondo" } },
    });
    await open(`/holdings/add?account=${EUR_ACCOUNT.id}`);
    searchFor(ISIN);
    fireEvent.click(await screen.findByRole("button", { name: "Add “Esempio Mondo ETF” (EUR)" }));
    const name = screen.getByLabelText("Name");
    await waitFor(() => expect(name).toHaveFocus());
    expect(name).toHaveValue("Esempio Mondo ETF");
    expect(name).toHaveAccessibleDescription(
      "Check the name: it comes from the price source and may be inaccurate.",
    );
    expect(screen.getByLabelText("ISIN (optional)")).toHaveValue(ISIN);
    expect(posts(calls, "/api/v1/assets")).toHaveLength(0);

    fireEvent.change(name, { target: { value: "Esempio Mondo" } });
    fireEvent.click(screen.getByRole("button", { name: "Create security" }));
    expect(
      await screen.findByText(
        "“Esempio Mondo” added. Automatic prices are being looked up in the background.",
      ),
    ).toBeInTheDocument();
    expect((posts(calls, "/api/v1/assets")[0] as { body?: unknown }).body).toEqual({
      name: "Esempio Mondo",
      asset_type: "ETF",
      quote_currency: "EUR",
      quote_unit: "CURRENCY",
      isin: ISIN,
    });
    const next = `account=${EUR_ACCOUNT.id}&asset=${CREATED.id}`;
    expect(screen.getByRole("link", { name: "Add holdings I already own" })).toHaveAttribute(
      "href",
      `/holdings/existing?${next}`,
    );
    expect(screen.getByRole("link", { name: "Add a transaction" })).toHaveAttribute(
      "href",
      `/transactions/new?${next}`,
    );
    // The form is empty again for the next one.
    expect(screen.getByLabelText("Name")).toHaveValue("");
  });

  it("offers your currency when no ISIN result is in it", async () => {
    const { calls } = setup({
      [SEARCH]: { body: [WORLD[2]] },
      [CREATE]: { status: 201, body: { ...CREATED, quote_currency: "USD" } },
    });
    await open(`/holdings/add?account=${USD_ACCOUNT.id}`);
    searchFor(ISIN);
    expect(
      await screen.findByText("Not in your currency? Create it by hand with this ISIN in USD."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create in USD" }));
    await waitFor(() => expect(screen.getByLabelText("Currency")).toHaveValue("USD"));
    expect(screen.getByLabelText("ISIN (optional)")).toHaveValue(ISIN);
    expect(screen.getByLabelText("The price is expressed in")).toHaveValue("CURRENCY");
    fireEvent.click(screen.getByRole("button", { name: "Create security" }));
    await screen.findByText(/added\./);
    expect((posts(calls, "/api/v1/assets")[0] as { body?: unknown }).body).toMatchObject({
      isin: ISIN,
      quote_currency: "USD",
      quote_unit: "CURRENCY",
    });
  });

  it("makes a text result a security with manual prices, its type to choose", async () => {
    const { calls } = setup({
      [SEARCH]: {
        body: [listing({ isin: null, symbol: "ZTXT.MI", name: "Esempio Testo SpA", asset_type: null })],
      },
      [CREATE]: {
        status: 201,
        body: { ...CREATED, isin: null, ticker: "ZTXT.MI", name: "Esempio Testo SpA" },
      },
    });
    await open();
    searchFor("esempio testo");
    const add = await screen.findByRole("button", { name: "Add “Esempio Testo SpA” (EUR)" });
    expect(add.closest("li")).toHaveTextContent(
      "No ISIN: manual prices only. Search by ISIN for automatic prices.",
    );
    expect(screen.queryByText(/Not in your currency\?/)).not.toBeInTheDocument();
    fireEvent.click(add);
    expect(screen.getByLabelText("Symbol (optional)")).toHaveValue("ZTXT.MI");
    expect(screen.getByLabelText("ISIN (optional)")).toHaveValue("");
    expect(screen.getByLabelText("Type")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Create security" }));
    await waitFor(() => expect(screen.getByLabelText("Type")).toHaveFocus());
    expect(posts(calls, "/api/v1/assets")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "STOCK" } });
    fireEvent.click(screen.getByRole("button", { name: "Create security" }));
    expect(
      await screen.findByText("“Esempio Testo SpA” added. No ISIN: you enter its prices yourself."),
    ).toBeInTheDocument();
  });

  it.each([
    [{ status: 429, body: { code: "TOO_MANY_ATTEMPTS" }, headers: { "Retry-After": "30" } },
      "Too many attempts. Try again in 1 minute."],
    [{ status: 503, body: { code: "MARKET_DATA_BUSY", retry_after: 20 }, headers: { "Retry-After": "20" } },
      "The price source is busy. Try again in 20 seconds."],
    [{ status: 503, body: { code: "MARKET_DATA_UNAVAILABLE" } },
      "The price source cannot answer now. Try again later, or create the security by hand."],
    [{ status: 503, body: { code: "MARKET_DATA_OFF" } },
      "Automatic prices are off on this installation: the search is not available. You can still create the security by hand."],
  ])("shows a refused search and keeps creating by hand (%#)", async (reply, text) => {
    setup({ [SEARCH]: reply });
    await open();
    searchFor(ISIN);
    expect((await screen.findByText(text)).closest("[role=alert]")).not.toBeNull();
    expect(screen.getByLabelText("Name")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Create security" })).toBeEnabled();
  });

  it("links to the security the user already has with that ISIN", async () => {
    const mine = { ...CREATED, id: "00000000-0000-4000-8000-0000000000b5", name: "Il mio Mondo" };
    setup({
      [SEARCH]: { body: WORLD },
      [CREATE]: { status: 409, body: { code: "ISIN_ALREADY_USED" } },
      "GET /api/v1/assets": { body: [mine] },
    });
    await open(`/holdings/add?account=${EUR_ACCOUNT.id}`);
    searchFor(ISIN);
    fireEvent.click(await screen.findByRole("button", { name: "Add “Esempio Mondo ETF” (EUR)" }));
    fireEvent.click(screen.getByRole("button", { name: "Create security" }));
    const note = await screen.findByText(/You already have this security: “Il mio Mondo”\./);
    expect(within(note).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      `/holdings/${mine.id}?account=${EUR_ACCOUNT.id}`,
    );
  });
});
