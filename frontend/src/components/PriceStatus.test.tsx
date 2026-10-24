import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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
