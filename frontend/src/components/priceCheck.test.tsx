import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AssetType, Me, PriceCheckResult, QuoteUnit } from "../api/types";
import i18n from "../i18n";
import { ME_KEY } from "../session";
import { type Reply, stubFetch } from "../test/fetchStub";
import { PriceCheck } from "./PriceCheck";

// Invented ISINs, symbols and prices only.
const ISIN = "ZZ0000087013";
const ROUTE = "POST /api/v1/assets/price-check";
const FOUND: PriceCheckResult = {
  outcome: "FOUND",
  reason: null,
  source: "yahoo",
  exchange: "Milan",
  exchange_code: "MIL",
  symbol: "ZWLD.MI",
  price: "101.75",
  price_date: "2026-11-17",
  currency: "EUR",
  quote_unit: "CURRENCY",
};
const answer = (over: Partial<PriceCheckResult>): Reply => ({ body: { ...FOUND, ...over } });

type Values = { isin: string; currency: string; unit: QuoteUnit; type: AssetType | null; ready: boolean };
const VALID: Values = { isin: ISIN, currency: "EUR", unit: "CURRENCY", type: "ETF", ready: true };

// Re-renders the check with other values, as the form does when a field changes.
let setValuesNow: (values: Values) => void = () => {};

function renderCheck(start: Values = VALID, marketData?: Me["market_data"]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (marketData) client.setQueryData(ME_KEY, { market_data: marketData } as Me);
  const view = (values: Values) => (
    <QueryClientProvider client={client}>
      <PriceCheck {...values} />
    </QueryClientProvider>
  );
  const { rerender } = render(view(start));
  setValuesNow = (values) => rerender(view(values));
}

const button = () => screen.getByRole("button", { name: /Check the price source|Checking…/ });
const region = () => screen.getByRole("status");

async function checkWith(reply: Reply, start: Values = VALID) {
  const stub = stubFetch({ [ROUTE]: reply });
  renderCheck(start);
  fireEvent.click(button());
  await waitFor(() => expect(button()).toHaveTextContent("Check the price source"));
  return stub;
}

afterEach(async () => {
  await i18n.changeLanguage("en");
});

describe("PriceCheck", () => {
  it("is disabled until the values are valid, and asks nothing while they change", () => {
    const { calls } = stubFetch({});
    renderCheck({ ...VALID, ready: false });
    expect(button()).toBeDisabled();
    setValuesNow({ ...VALID, isin: "ZZ00000870", ready: false });
    setValuesNow(VALID);
    expect(button()).toBeEnabled();
    expect(calls).toEqual([]); // never automatic
  });

  it("sends one request per click, with the form's values", async () => {
    const { calls } = await checkWith(answer({}), { ...VALID, currency: "GBP", unit: "MINOR_CURRENCY_UNIT" });
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({
      isin: ISIN,
      quote_currency: "GBP",
      quote_unit: "MINOR_CURRENCY_UNIT",
      asset_type: "ETF",
    });
  });

  it("allows no second click while a check runs, and announces it", async () => {
    let release: (reply: Reply) => void = () => {};
    const { calls } = stubFetch({ [ROUTE]: () => new Promise<Reply>((done) => (release = done)) });
    renderCheck();
    fireEvent.click(button());
    fireEvent.click(button());
    expect(button()).toBeDisabled();
    expect(button()).toHaveTextContent("Checking…");
    expect(region()).toHaveTextContent("Checking…");
    release(answer({}));
    await waitFor(() => expect(button()).toBeEnabled());
    expect(calls).toHaveLength(1);
  });

  it("names the source, the exchange and the latest price, then says it is expected", async () => {
    await checkWith(answer({}));
    expect(region()).toHaveTextContent(
      "Expected source: Yahoo Finance · Milan · latest price €101.75 on Nov 17, 2026",
    );
    expect(region()).toHaveTextContent("The final source is chosen after you save.");
  });

  it("gives the price in the security's unit, and in Italian", async () => {
    await i18n.changeLanguage("it");
    stubFetch({ [ROUTE]: answer({ price: "8675", currency: "GBP", quote_unit: "MINOR_CURRENCY_UNIT", exchange: "London", exchange_code: "LSE" }) });
    renderCheck();
    fireEvent.click(screen.getByRole("button", { name: "Controlla la fonte prezzi" }));
    await waitFor(() => expect(region()).toHaveTextContent(/^Fonte prevista: Yahoo Finance · /));
    expect(region()).toHaveTextContent(/ultimo prezzo 8\.675,00 centesimi di GBP del 17 nov 2026/);
    expect(region()).toHaveTextContent("La fonte definitiva viene scelta dopo il salvataggio.");
  });

  it("says when the source shows no price", async () => {
    await checkWith(answer({ price: null, price_date: null }));
    expect(region()).toHaveTextContent("Expected source: Yahoo Finance · Milan (no price available)");
    expect(region()).toHaveTextContent("The final source is chosen after you save.");
  });

  it("shows an unknown engine as it is named", async () => {
    await checkWith(answer({ source: "other-engine", exchange_code: null, exchange: "Somewhere" }));
    expect(region()).toHaveTextContent("Expected source: other-engine · Somewhere · latest price");
  });

  it.each([
    ["NOT_FOUND", "No source: prices are entered by hand. No provider knows this code."],
    ["NO_ENGINE", "No source: prices are entered by hand. No active source covers this kind of security."],
  ] as const)("NO_SOURCE %s", async (reason, text) => {
    await checkWith(answer({ outcome: "NO_SOURCE", reason, source: null, exchange: null, price: null }));
    expect(region()).toHaveTextContent(text);
    expect(region()).not.toHaveTextContent("after you save");
  });

  it.each([
    ["CURRENCY", "The provider has this security in another currency: check the currency you chose."],
    ["UNIT", "The provider quotes this security in another unit: check the type."],
  ] as const)("MISMATCH %s", async (reason, text) => {
    await checkWith(answer({ outcome: "MISMATCH", reason, source: null, exchange: null, price: null }));
    expect(region()).toHaveTextContent(text);
  });

  it.each([
    [429, "TOO_MANY_ATTEMPTS", { "Retry-After": "42" }, "Too many checks: try again in 42 seconds."],
    [429, "TOO_MANY_ATTEMPTS", { "Retry-After": "1" }, "Too many checks: try again in 1 second."],
    [429, "TOO_MANY_ATTEMPTS", {}, "Too many attempts. Try again later."],
    [503, "MARKET_DATA_BUSY", { "Retry-After": "5" }, "Other checks are running: try again in 5 seconds."],
    [503, "MARKET_DATA_BUSY", {}, "Try again later."],
    [503, "MARKET_DATA_UNAVAILABLE", {}, "The providers are not answering now: try again soon."],
    [503, "MARKET_DATA_UNAVAILABLE", { "Retry-After": "9" }, "The providers are not answering now: try again soon."],
  ])("error %s %s %j", async (status, code, headers, text) => {
    await checkWith({ status, headers, body: { code, detail: "x" } });
    expect(region()).toHaveTextContent(text);
  });

  it("words the market data being off as the app does", async () => {
    await checkWith({ status: 503, body: { code: "MARKET_DATA_OFF", detail: "x" } });
    expect(region().textContent).not.toBe("");
  });

  it.each([
    ["the ISIN", { isin: "ZZ0000087021" }],
    ["the currency", { currency: "USD" }],
    ["the unit", { unit: "MINOR_CURRENCY_UNIT" as QuoteUnit }],
    ["the type", { type: "STOCK" as AssetType }],
  ])("clears the answer when %s changes", async (_, change) => {
    await checkWith(answer({}));
    expect(region()).toHaveTextContent("Expected source");
    setValuesNow({ ...VALID, ...change });
    expect(region()).toHaveTextContent(/^$/);
    setValuesNow(VALID); // back to the values asked: the old answer does not come back
    expect(region()).toHaveTextContent(/^$/);
  });

  it("never shows a late answer beside values changed meanwhile", async () => {
    let release: (reply: Reply) => void = () => {};
    stubFetch({ [ROUTE]: () => new Promise<Reply>((done) => (release = done)) });
    renderCheck();
    fireEvent.click(button());
    setValuesNow({ ...VALID, currency: "USD" });
    release(answer({}));
    await waitFor(() => expect(button()).toBeEnabled());
    expect(region()).toHaveTextContent(/^$/);
  });

  it("is hidden when market data is off", () => {
    stubFetch({});
    renderCheck(VALID, "OFF");
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("is shown when market data is on", () => {
    stubFetch({});
    renderCheck(VALID, "ON");
    expect(button()).toBeEnabled();
  });
});

describe("PriceCheck without every value (fix of the 1.1.0 review)", () => {
  it("stays disabled without a currency, even when the form says it is ready", () => {
    const { calls } = stubFetch({});
    renderCheck({ ...VALID, currency: "" });
    expect(button()).toBeDisabled();
    fireEvent.click(button());
    setValuesNow({ ...VALID, type: null });
    expect(button()).toBeDisabled();
    expect(calls).toEqual([]);
  });
});
