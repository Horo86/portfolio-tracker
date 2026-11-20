import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Asset } from "../api/types";
import { stubFetch } from "../test/fetchStub";
import { AssetCreateForm } from "./AssetCreateForm";

// Invented ISINs, names and symbols only.
const CREATED: Asset = {
  id: "00000000-0000-4000-8000-0000000000b9",
  isin: "ZZ0000087013",
  ticker: null,
  name: "Esempio Mondo ETF",
  asset_type: "ETF",
  quote_currency: "EUR",
  quote_unit: "CURRENCY",
  shared: false,
  in_use: false,
  created_at: "2026-10-20T09:00:00Z",
};

function renderForm(props: Partial<Parameters<typeof AssetCreateForm>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onCreated = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <AssetCreateForm defaultCurrency="EUR" onCreated={onCreated} onCancel={() => {}} {...props} />
    </QueryClientProvider>,
  );
  return { onCreated };
}

describe("AssetCreateForm", () => {
  it("starts from the values it is given, every one editable", async () => {
    const { calls } = stubFetch({
      "GET /api/v1/currencies": { body: ["EUR", "GBP", "USD"] },
      "POST /api/v1/assets": { status: 201, body: CREATED },
    });
    const { onCreated } = renderForm({
      initial: {
        name: "Esempio Mondo",
        asset_type: "ETF",
        quote_currency: "GBP",
        quote_unit: "MINOR_CURRENCY_UNIT",
        isin: "ZZ0000087013",
        ticker: "ZWLD.L",
      },
    });
    expect(screen.getByLabelText("Name")).toHaveValue("Esempio Mondo");
    expect(screen.getByLabelText("Type")).toHaveValue("ETF");
    await waitFor(() => expect(screen.getByLabelText("Currency")).toHaveValue("GBP"));
    expect(screen.getByLabelText("ISIN (optional)")).toHaveValue("ZZ0000087013");
    expect(screen.getByLabelText("Symbol (optional)")).toHaveValue("ZWLD.L");
    expect(screen.getByLabelText("The price is expressed in")).toHaveValue("MINOR_CURRENCY_UNIT");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Esempio Mondo ETF" } });
    fireEvent.click(screen.getByRole("button", { name: "Create security" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "Esempio Mondo ETF",
      asset_type: "ETF",
      quote_currency: "GBP",
      quote_unit: "MINOR_CURRENCY_UNIT",
      isin: "ZZ0000087013",
      ticker: "ZWLD.L",
    });
  });

  it("asks for the type when none was proposed, and sends nothing until it is chosen", async () => {
    const { calls } = stubFetch({
      "GET /api/v1/currencies": { body: ["EUR"] },
      "POST /api/v1/assets": { status: 201, body: { ...CREATED, asset_type: "STOCK" } },
    });
    const { onCreated } = renderForm({ initial: { name: "Esempio Testo SpA", asset_type: null } });
    const typeSelect = screen.getByLabelText("Type");
    expect(typeSelect).toHaveValue("");
    expect(screen.getByRole("option", { name: "Choose the type" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Create security" }));
    expect(await screen.findByText("Fill in this field.")).toBeInTheDocument();
    expect(typeSelect).toHaveAttribute("aria-invalid", "true");
    expect(typeSelect).toHaveFocus();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);

    fireEvent.change(typeSelect, { target: { value: "STOCK" } });
    fireEvent.click(screen.getByRole("button", { name: "Create security" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ asset_type: "STOCK" });
  });

  it.each([
    ["without an ISIN", ""],
    ["with an ISIN", "ZZ0000087013"],
  ])("two quick clicks on “Create security” create it once, %s", async (_, isin) => {
    let created = false;
    const { calls } = stubFetch({
      "GET /api/v1/currencies": { body: ["EUR"] },
      // As the server: an ISIN can be one of the user's securities only once.
      "POST /api/v1/assets": () => {
        if (created && isin) return { status: 409, body: { code: "ISIN_ALREADY_USED" } };
        created = true;
        return { status: 201, body: { ...CREATED, isin: isin || null } };
      },
    });
    const { onCreated } = renderForm({ initial: { name: "Esempio Mondo ETF", isin } });
    const button = screen.getByRole("button", { name: "Create security" });
    // Two clicks in one task, with no render between them (outside act).
    const click = () => button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    click();
    click();
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    await waitFor(() => expect(button).toBeEnabled());
    expect(screen.queryByText(/You already have a security with this ISIN/)).toBeNull();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    expect(onCreated).toHaveBeenCalledTimes(1);
  });

  it("says, as the ISIN is typed, whether the security gets automatic prices (Obs 2)", () => {
    renderForm();
    const isin = screen.getByLabelText("ISIN (optional)");
    expect(isin).toHaveAccessibleDescription("Without an ISIN you enter its prices yourself.");
    fireEvent.change(isin, { target: { value: "ZZ00000" } }); // not an ISIN yet
    expect(isin).toHaveAccessibleDescription("With an ISIN the app looks for automatic prices.");
    fireEvent.change(isin, { target: { value: "ZZ0000087013" } });
    expect(isin).toHaveAccessibleDescription(
      "With this ISIN the app looks up automatic prices after saving, on an exchange it chooses.",
    );
    fireEvent.change(isin, { target: { value: "" } });
    expect(isin).toHaveAccessibleDescription("Without an ISIN you enter its prices yourself.");
  });

  it("creating from scratch keeps an ETF as the type", () => {
    stubFetch({ "GET /api/v1/currencies": { body: ["EUR"] } });
    renderForm();
    expect(screen.getByLabelText("Type")).toHaveValue("ETF");
    expect(screen.queryByRole("option", { name: "Choose the type" })).not.toBeInTheDocument();
  });

  it("can take the focus on the name, with a hint about it", () => {
    stubFetch({ "GET /api/v1/currencies": { body: ["EUR"] } });
    renderForm({ initial: { name: "Esempio Mondo" }, nameHint: "Check the name.", focusName: true });
    const name = screen.getByLabelText("Name");
    expect(name).toHaveFocus();
    expect(name).toHaveAccessibleDescription("Check the name.");
  });


  // The live price check (D-178): wired to the form's own values, never automatic.
  describe("price check", () => {
    const CHECK = "POST /api/v1/assets/price-check";
    const FOUND = {
      outcome: "FOUND", reason: null, source: "yahoo", exchange: "Milan", exchange_code: "MIL",
      symbol: "ZWLD.MI", price: "101.75", price_date: "2026-11-17", currency: "EUR",
      quote_unit: "CURRENCY",
    }; // prettier-ignore
    const checkButton = () => screen.getByRole("button", { name: "Check the price source" });

    it("waits for a valid ISIN and a type, then sends the form's values on a click", async () => {
      const { calls } = stubFetch({
        "GET /api/v1/currencies": { body: ["EUR", "GBP"] },
        [CHECK]: { body: FOUND },
      });
      renderForm({ initial: { name: "Esempio Mondo", asset_type: null } });
      expect(checkButton()).toBeDisabled();
      const isin = screen.getByLabelText("ISIN (optional)");
      fireEvent.change(isin, { target: { value: "zz0000087014" } }); // a wrong check digit
      expect(checkButton()).toBeDisabled();
      fireEvent.change(isin, { target: { value: " zz0000087013 " } });
      expect(checkButton()).toBeDisabled(); // no type yet
      fireEvent.change(screen.getByLabelText("Type"), { target: { value: "BOND" } });
      expect(checkButton()).toBeEnabled();
      expect(calls.filter((c) => c.path.endsWith("/price-check"))).toEqual([]); // nothing while typing
      fireEvent.click(checkButton());
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Expected source"));
      expect(calls.filter((c) => c.path.endsWith("/price-check")).map((c) => c.body)).toEqual([
        { isin: "ZZ0000087013", quote_currency: "EUR", quote_unit: "PERCENT_OF_NOMINAL", asset_type: "BOND" },
      ]);
    });

    it("stays disabled until a currency is set as well as the ISIN and the type", () => {
      const { calls } = stubFetch({ "GET /api/v1/currencies": { body: ["EUR"] } });
      renderForm({ defaultCurrency: "", initial: { name: "Esempio Mondo", asset_type: "ETF" } });
      fireEvent.change(screen.getByLabelText("ISIN (optional)"), { target: { value: "ZZ0000087013" } });
      expect(checkButton()).toBeDisabled();
      expect(calls.filter((c) => c.path.endsWith("/price-check"))).toEqual([]);
    });

    it("clears the answer when a field that feeds it changes, and fills in nothing", async () => {
      stubFetch({ "GET /api/v1/currencies": { body: ["EUR"] }, [CHECK]: { body: FOUND } });
      renderForm({ initial: { name: "Esempio Mondo", isin: "ZZ0000087013" } });
      fireEvent.click(checkButton());
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Expected source"));
      expect(screen.getByLabelText("Symbol (optional)")).toHaveValue(""); // never filled
      fireEvent.change(screen.getByLabelText("Type"), { target: { value: "STOCK" } });
      expect(screen.getByRole("status")).toHaveTextContent(/^$/);
      fireEvent.click(checkButton());
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Expected source"));
      fireEvent.change(screen.getByLabelText("ISIN (optional)"), { target: { value: "ZZ000008701" } });
      expect(screen.getByRole("status")).toHaveTextContent(/^$/);
    });

    it("never holds the save back while a check runs", async () => {
      const { calls } = stubFetch({
        "GET /api/v1/currencies": { body: ["EUR"] },
        [CHECK]: () => new Promise(() => {}), // never answers
        "POST /api/v1/assets": { status: 201, body: CREATED },
      });
      const { onCreated } = renderForm({ initial: { name: "Esempio Mondo ETF", isin: "ZZ0000087013" } });
      fireEvent.click(checkButton());
      fireEvent.click(screen.getByRole("button", { name: "Create security" }));
      await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED));
      expect(calls.find((c) => c.path === "/api/v1/assets")?.body).toEqual({
        name: "Esempio Mondo ETF", asset_type: "ETF", quote_currency: "EUR",
        quote_unit: "CURRENCY", isin: "ZZ0000087013",
      }); // prettier-ignore
    });
  });
});
