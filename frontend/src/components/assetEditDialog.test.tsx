import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Asset } from "../api/types";
import i18n from "../i18n";
import { type Call, type Handler, stubFetch } from "../test/fetchStub";
import { useAssets } from "../transactions";
import { AssetEditDialog } from "./AssetEditDialog";

// Invented ISINs, names and symbols only.
const UNUSED: Asset = {
  id: "00000000-0000-4000-8000-0000000000c1",
  isin: "ZZ0000087013",
  ticker: "ZWLD",
  name: "Esempio Mondo ETF",
  asset_type: "ETF",
  quote_currency: "EUR",
  quote_unit: "CURRENCY",
  shared: false,
  in_use: false,
  created_at: "2026-10-20T09:00:00Z",
};
const USED: Asset = { ...UNUSED, in_use: true };
const PATCH = `PATCH /api/v1/assets/${UNUSED.id}`;

/** The dialog over the user's list, as the holding detail shows it: a refetch reaches it. */
function Harness(props: { marketData: "ON" | "OFF"; onSaved: (asset: Asset) => void }) {
  const assets = useAssets();
  const asset = assets.data?.find((a) => a.id === UNUSED.id);
  return asset ? <AssetEditDialog asset={asset} onClose={() => {}} onDelete={() => {}} {...props} /> : null;
}

function renderDialog(routes: Record<string, Handler>, marketData: "ON" | "OFF" = "ON") {
  const stub = stubFetch({ "GET /api/v1/currencies": { body: ["EUR", "GBP", "USD"] }, ...routes });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onSaved = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <Harness marketData={marketData} onSaved={onSaved} />
    </QueryClientProvider>,
  );
  return { ...stub, onSaved };
}

const patches = (calls: Call[]) => calls.filter((c) => c.method === "PATCH");

describe("AssetEditDialog (D-150)", () => {
  it("fills in every field from the security; with no transaction every one is saved", async () => {
    const saved = { ...UNUSED, quote_currency: "GBP", quote_unit: "MINOR_CURRENCY_UNIT" as const };
    const { calls, onSaved } = renderDialog({
      "GET /api/v1/assets": { body: [UNUSED] },
      [PATCH]: { body: saved },
    });
    const dialog = await screen.findByRole("dialog", { name: "Edit security" });
    expect(screen.getByLabelText("Name")).toHaveValue("Esempio Mondo ETF");
    expect(screen.getByLabelText("Type")).toHaveValue("ETF");
    expect(screen.getByLabelText("ISIN (optional)")).toHaveValue("ZZ0000087013");
    expect(screen.getByLabelText("Symbol (optional)")).toHaveValue("ZWLD");
    expect(screen.getByLabelText("The price is expressed in")).toHaveValue("CURRENCY");
    await waitFor(() => expect(screen.getByLabelText("Currency")).toHaveValue("EUR"));
    for (const label of ["Type", "Currency", "ISIN (optional)", "The price is expressed in"]) {
      expect(screen.getByLabelText(label)).toBeEnabled();
    }
    expect(dialog).not.toHaveTextContent(/can change only while/);

    fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "GBP" } });
    fireEvent.change(screen.getByLabelText("The price is expressed in"), {
      target: { value: "MINOR_CURRENCY_UNIT" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(patches(calls)[0].body).toEqual({
      name: "Esempio Mondo ETF",
      ticker: "ZWLD",
      asset_type: "ETF",
      quote_currency: "GBP",
      quote_unit: "MINOR_CURRENCY_UNIT",
      isin: "ZZ0000087013",
    });
  });

  it("locks type, currency, ISIN and unit while a transaction uses it, and names them", async () => {
    const { calls, onSaved } = renderDialog({
      "GET /api/v1/assets": { body: [USED] },
      [PATCH]: { body: { ...USED, name: "Esempio Mondo" } },
    });
    const dialog = await screen.findByRole("dialog", { name: "Edit security" });
    expect(dialog).toHaveTextContent(
      "Type, currency, price unit and ISIN can change only while no transaction uses the security.",
    );
    for (const label of ["Type", "Currency", "ISIN (optional)", "The price is expressed in"]) {
      expect(screen.getByLabelText(label)).toBeDisabled();
    }
    expect(screen.getByLabelText("ISIN (optional)")).toHaveValue("ZZ0000087013");
    expect(screen.getByLabelText("Name")).toBeEnabled();
    expect(screen.getByLabelText("Symbol (optional)")).toBeEnabled();

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Esempio Mondo" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(patches(calls)[0].body).toEqual({ name: "Esempio Mondo", ticker: "ZWLD" });
  });

  it("sends a cleared ISIN as none and a cleared symbol as empty", async () => {
    const { calls, onSaved } = renderDialog({
      "GET /api/v1/assets": { body: [UNUSED] },
      [PATCH]: { body: { ...UNUSED, isin: null, ticker: null } },
    });
    fireEvent.change(await screen.findByLabelText("ISIN (optional)"), { target: { value: " " } });
    fireEvent.change(screen.getByLabelText("Symbol (optional)"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(patches(calls)[0].body).toMatchObject({ isin: null, ticker: "" });
  });

  it("says prices are looked up again only with market data on, an ISIN and the fields free", async () => {
    const note = "With another ISIN or currency, automatic prices are looked up again.";
    renderDialog({ "GET /api/v1/assets": { body: [UNUSED] } });
    const isin = await screen.findByLabelText("ISIN (optional)");
    expect(screen.getByText(note)).toBeInTheDocument();
    fireEvent.change(isin, { target: { value: "" } });
    expect(screen.queryByText(note)).toBeNull();
  });

  it("never says prices are looked up again with market data off or the fields locked", async () => {
    const note = /automatic prices are looked up again/;
    renderDialog({ "GET /api/v1/assets": { body: [UNUSED] } }, "OFF");
    await screen.findByRole("dialog", { name: "Edit security" });
    expect(screen.queryByText(note)).toBeNull();
  });

  it("never says prices are looked up again for a security in use", async () => {
    renderDialog({ "GET /api/v1/assets": { body: [USED] } });
    await screen.findByRole("dialog", { name: "Edit security" });
    expect(screen.queryByText(/automatic prices are looked up again/)).toBeNull();
  });

  it("names the fields a transaction locked meanwhile (409 ASSET_IN_USE), then locks them", async () => {
    let used = false;
    const { calls } = renderDialog({
      "GET /api/v1/assets": () => ({ body: [used ? USED : UNUSED] }),
      [PATCH]: () => {
        used = true; // a transaction saved in another tab
        return { status: 409, body: { code: "ASSET_IN_USE", fields: ["asset_type", "quote_currency"] } };
      },
    });
    const currency = await screen.findByLabelText("Currency");
    await waitFor(() => expect(currency).toHaveValue("EUR"));
    fireEvent.change(currency, { target: { value: "USD" } });
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "STOCK" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Transactions now use this security: type and currency can no longer change.",
    );
    // The list is asked again: the fields lock and show what is stored.
    await waitFor(() => expect(screen.getByLabelText("Currency")).toBeDisabled());
    expect(screen.getByLabelText("Currency")).toHaveValue("EUR");
    expect(screen.getByLabelText("Type")).toHaveValue("ETF");
    expect(patches(calls)).toHaveLength(1);
  });

  it.each([
    [["quote_currency"], "Il titolo è ora usato da transazioni: la valuta non si può più cambiare."],
    [["isin", "asset_type"], "Il titolo è ora usato da transazioni: il tipo e l'ISIN non si possono più cambiare."],
    [["quote_unit", "quote_currency", "asset_type"],
     "Il titolo è ora usato da transazioni: il tipo, la valuta e l'unità di prezzo non si possono più cambiare."],
  ])("names in Italian, each with its article, the fields locked meanwhile (%j)", async (fields, text) => {
    await i18n.changeLanguage("it");
    renderDialog({
      "GET /api/v1/assets": { body: [UNUSED] },
      [PATCH]: { status: 409, body: { code: "ASSET_IN_USE", fields } },
    });
    await screen.findByRole("dialog", { name: "Modifica titolo" });
    fireEvent.click(screen.getByRole("button", { name: "Salva" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });

  it("says why a currency or unit change over manual prices is refused (409 ASSET_HAS_PRICES)", async () => {
    renderDialog({
      "GET /api/v1/assets": { body: [UNUSED] },
      [PATCH]: { status: 409, body: { code: "ASSET_HAS_PRICES", fields: ["quote_currency"] } },
    });
    const currency = await screen.findByLabelText("Currency");
    await waitFor(() => expect(currency).toHaveValue("EUR"));
    fireEvent.change(currency, { target: { value: "GBP" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You have manual prices for this security: delete them before changing its currency or unit.",
    );
  });

  it("shows an ISIN already used at the ISIN field", async () => {
    renderDialog({
      "GET /api/v1/assets": { body: [UNUSED] },
      [PATCH]: { status: 409, body: { code: "ISIN_ALREADY_USED" } },
    });
    const isin = await screen.findByLabelText("ISIN (optional)");
    fireEvent.change(isin, { target: { value: "ZZ0000087021" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("You already have a security with this ISIN.")).toBeInTheDocument();
    expect(isin).toHaveAttribute("aria-invalid", "true");
    expect(isin).toHaveFocus();
  });

  it("gives a bond its price in % of nominal", async () => {
    renderDialog({ "GET /api/v1/assets": { body: [UNUSED] } });
    fireEvent.change(await screen.findByLabelText("Type"), { target: { value: "BOND" } });
    const unit = screen.getByLabelText("The price is expressed in");
    expect(unit).toHaveValue("PERCENT_OF_NOMINAL");
    expect(unit.querySelectorAll("option")).toHaveLength(1);
  });

  it("asks for the name and sends nothing without it", async () => {
    const { calls } = renderDialog({ "GET /api/v1/assets": { body: [UNUSED] } });
    const name = await screen.findByLabelText("Name");
    fireEvent.change(name, { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Fill in this field.")).toBeInTheDocument();
    expect(name).toHaveFocus();
    expect(patches(calls)).toHaveLength(0);
  });

  it("two quick clicks on Save send one change", async () => {
    const { calls, onSaved } = renderDialog({
      "GET /api/v1/assets": { body: [UNUSED] },
      [PATCH]: { body: UNUSED },
    });
    await screen.findByLabelText("Name");
    const button = screen.getByRole("button", { name: "Save" });
    // Two clicks in one task, with no render between them (outside act).
    const click = () => button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    click();
    click();
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(patches(calls)).toHaveLength(1);
  });
});
