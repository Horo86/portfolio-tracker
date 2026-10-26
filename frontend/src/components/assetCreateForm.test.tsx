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
});
