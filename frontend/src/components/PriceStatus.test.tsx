import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import i18n from "../i18n";
import { WORLD_ETF } from "../test/holdings";
import { PriceNotes } from "./PriceStatus";

const show = (holding: Partial<typeof WORLD_ETF>, props: Record<string, unknown> = {}) =>
  render(<PriceNotes holding={{ ...WORLD_ETF, ...holding }} marketData="ON" {...props} />);

describe("PriceNotes", () => {
  it("says nothing about automatic prices that work, unless asked", () => {
    const { container } = show({});
    expect(container).toBeEmptyDOMElement();
    show({}, { showOk: true });
    expect(screen.getByText("Automatic prices")).toHaveClass("pill-ok");
  });

  it("shows a status that is not OK, with its tone", () => {
    show({ price_status: "PENDING" });
    expect(screen.getByText("Looking up prices")).toHaveClass("pill-soft");
    show({ price_status: "FAILING", price_status_detail: "NOT_LISTED" }, { form: "long" });
    expect(
      screen.getByText(/The price source no longer finds this security/),
    ).toHaveClass("pill-warning");
  });

  it("names the exchange and the latest close of automatic prices in the long form (Obs 4)", () => {
    show({}, { form: "long", showOk: true });
    expect(screen.getByText("Automatic prices from Milan: latest close on Oct 20, 2026.")).toHaveClass(
      "pill-ok",
    );
  });

  it("keeps the general sentence without a provider close, and in the short form", () => {
    show({ price_exchange: null }, { form: "long", showOk: true });
    expect(screen.getByText("Automatic prices, updated daily.")).toBeInTheDocument();
    show({ price_source: "MANUAL", price_exchange: null }, { form: "long", showOk: true });
    expect(screen.getAllByText("Automatic prices, updated daily.")).toHaveLength(2);
    show({}, { showOk: true }); // a pill in a table: short, as before
    expect(screen.getByText("Automatic prices")).toBeInTheDocument();
  });

  it("dates the older prices kept in use after a failed update (Obs 4)", () => {
    show({ price_status: "FAILING" }, { form: "long" });
    expect(
      screen.getByText(
        "The last update failed: older prices stay in use (latest close on Oct 20, 2026, Milan).",
      ),
    ).toHaveClass("pill-warning");
    show({ price_status: "FAILING", price_status_detail: "NOT_LISTED" }, { form: "long" });
    expect(
      screen.getByText(/older prices stay in use \(latest close on Oct 20, 2026, Milan\); enter the final/),
    ).toBeInTheDocument();
    show({ price_status: "FAILING", price_date: null }, { form: "long" });
    expect(screen.getByText("The last update failed: older prices stay in use.")).toBeInTheDocument();
  });

  it("leaves the other statuses as they were", () => {
    show({ price_status: "MISMATCH", price_status_detail: "LEVEL" }, { form: "long" });
    expect(screen.getByText(/do not match the previous ones/)).toBeInTheDocument();
    expect(screen.queryByText(/latest close/)).not.toBeInTheDocument();
  });

  it("says it in Italian too, with the exchange named in Italian when it is known", async () => {
    await i18n.changeLanguage("it");
    show({ price_exchange_code: "MIL" }, { form: "long", showOk: true });
    expect(screen.getByText(/^Prezzi automatici da .+: ultima chiusura del 20 ott 2026\.$/)).toBeInTheDocument();
    show({ price_status: "FAILING" }, { form: "long" });
    expect(screen.getByText(/restano in uso i prezzi precedenti \(ultima chiusura del 20 ott 2026, Milan\)\./)).toBeInTheDocument();
  });

  it("says when automatic prices are off on the installation (D-145)", () => {
    render(<PriceNotes holding={{ ...WORLD_ETF, price_status: "PENDING" }} marketData="OFF" />);
    expect(screen.getByText("Automatic prices off")).toBeInTheDocument();
    expect(screen.queryByText("Looking up prices")).not.toBeInTheDocument();
  });

  it("names the date of a stale price, and the next daily update only when one can help", () => {
    show({ price_stale: true, price_date: "2026-10-10" });
    expect(screen.getByText("Price not updated since Oct 10, 2026 · next daily update")).toBeInTheDocument();
    show({ price_stale: true, price_date: "2026-10-10", price_status: "NOT_AVAILABLE" });
    expect(screen.getByText("Price not updated since Oct 10, 2026")).toBeInTheDocument();
  });

  it("marks a manual close, an estimate at cost and a missing value", () => {
    show({ price_source: "MANUAL" });
    expect(screen.getByText("Manual price")).toBeInTheDocument();
    show({ isin: null, price_status: "MANUAL", price_source: "MANUAL" });
    expect(screen.getAllByText("Manual price")).toHaveLength(1); // "by hand only" says it already
    expect(screen.getByText("Prices by hand only")).toBeInTheDocument();
    show({ estimated_at_cost: true, price: null, price_date: null, price_source: null });
    expect(screen.getByText("Estimated at cost")).toBeInTheDocument();
    show({ value_unavailable: true });
    expect(screen.getByText("Value unavailable: the exchange rate is missing")).toBeInTheDocument();
  });
});
