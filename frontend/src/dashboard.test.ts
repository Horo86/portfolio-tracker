import { describe, expect, it } from "vitest";

import type { StaleFx } from "./api/types";
import { bestWorst, largest, priceNotices, uniquePairs } from "./dashboard";
import type { HoldingRow } from "./holdings";
import { portfolioRow, WORLD_ETF } from "./test/holdings";

// Invented rows: only the fields each rule reads differ from the world ETF.
let serial = 0;
function row(name: string, extra: Partial<HoldingRow> = {}): HoldingRow {
  serial += 1;
  const id = `00000000-0000-4000-8000-${String(serial).padStart(12, "0")}`;
  return { ...portfolioRow(WORLD_ETF), asset_id: id, name, ...extra };
}

describe("priceNotices (D-153 (6), Q-15)", () => {
  it("lists the open securities whose price needs the user, in their order", () => {
    const rows = [
      row("A ok"),
      row("B failing", { price_status: "FAILING" }),
      row("C delisted", { price_status: "FAILING", price_status_detail: "NOT_LISTED" }),
      row("D mismatch", { price_status: "MISMATCH", price_status_detail: "CURRENCY" }),
      row("E no source", { price_status: "NOT_AVAILABLE" }),
      row("F stale", { price_stale: true, price_date: "2026-10-10" }),
      row("G closed", { price_status: "FAILING", open: false }),
      row("H pending", { price_status: "PENDING" }),
      row("I manual only", { isin: null, price_status: "MANUAL" }),
    ];
    const { shown, more, pending, off } = priceNotices(rows, "ON");
    expect(shown.map((n) => [n.row.name, n.code, n.stale])).toEqual([
      ["B failing", "FAILING", null],
      ["C delisted", "NOT_LISTED", null],
      ["D mismatch", "MISMATCH_CURRENCY", null],
      ["E no source", "NOT_AVAILABLE", null],
      ["F stale", null, "priceStatus.staleNext"],
    ]);
    expect(more).toBe(0);
    expect(pending).toBe(1); // a quiet note, not a line
    expect(off).toBe(false);
  });

  it("puts a status and a stale price of one security on one line", () => {
    const rows = [row("Failing and stale", { price_status: "FAILING", price_stale: true })];
    const { shown } = priceNotices(rows, "ON");
    expect(shown).toHaveLength(1);
    expect(shown[0]).toMatchObject({ code: "FAILING", stale: "priceStatus.staleNext" });
  });

  it("with market data off says it once and keeps only stale prices by hand (D-145)", () => {
    const rows = [
      row("A isin, failing", { price_status: "FAILING" }),
      row("B isin, stale manual", { price_stale: true, price_source: "MANUAL" }),
      row("C no isin", { isin: null, price_status: "MANUAL" }),
    ];
    const { shown, pending, off } = priceNotices(rows, "OFF");
    expect(shown.map((n) => [n.row.name, n.code, n.stale])).toEqual([
      ["B isin, stale manual", null, "priceStatus.stale"],
    ]);
    expect(pending).toBe(0);
    expect(off).toBe(true);
  });

  it("says nothing about market data off when no open security has an ISIN", () => {
    const rows = [row("No isin", { isin: null, price_status: "MANUAL" })];
    expect(priceNotices(rows, "OFF").off).toBe(false);
  });

  it("shows up to 6 lines, then 5 and how many more", () => {
    const failing = (n: number) =>
      Array.from({ length: n }, (_, i) => row(`S${i}`, { price_status: "FAILING" }));
    expect(priceNotices(failing(6), "ON")).toMatchObject({ more: 0 });
    expect(priceNotices(failing(6), "ON").shown).toHaveLength(6);
    const seven = priceNotices(failing(7), "ON");
    expect(seven.shown.map((n) => n.row.name)).toEqual(["S0", "S1", "S2", "S3", "S4"]);
    expect(seven.more).toBe(2);
  });
});

describe("uniquePairs (D-153 (6))", () => {
  const fx = (from: string, to: string, day: string): StaleFx => ({
    from_currency: from,
    to_currency: to,
    rate_date: day,
  });

  it("counts a pair and its inverse once, with the older date", () => {
    const notices = [
      fx("USD", "EUR", "2026-10-12"),
      fx("EUR", "USD", "2026-10-09"),
      fx("USD", "EUR", "2026-10-12"),
      fx("GBP", "EUR", "2026-10-15"),
    ];
    expect(uniquePairs(notices)).toEqual([
      fx("USD", "EUR", "2026-10-09"),
      fx("GBP", "EUR", "2026-10-15"),
    ]);
  });
});

describe("bestWorst (D-153 (5), §13)", () => {
  const pct = (name: string, unrealized_pct: string, extra: Partial<HoldingRow> = {}) =>
    row(name, { unrealized_pct, ...extra });

  it("ranks open positions with a real price by unrealized %, best first", () => {
    const rows = [
      pct("Low", "-0.094"),
      pct("High", "0.382"),
      pct("At cost", "0", { estimated_at_cost: true }),
      pct("No rate", "0.5", { value_unavailable: true }),
      pct("Part without rate", "0.6", { partial: true }),
      pct("Closed", "0.9", { open: false }),
      pct("No base", "0", { unrealized_pct: null }),
      pct("Mid", "0.129"),
      pct("Tiny", "0.12900001"),
    ];
    const { ranked, atCost } = bestWorst(rows);
    expect(ranked.map((r) => r.name)).toEqual(["High", "Tiny", "Mid", "Low"]);
    expect(atCost).toBe(1);
  });

  it("keeps 3 best and 3 worst past 6, never the same security twice", () => {
    const rows = ["0.1", "0.7", "-0.2", "0.3", "0.5", "-0.4", "0.2"].map((p, i) =>
      pct(`P${i} ${p}`, p),
    );
    expect(bestWorst(rows).ranked.map((r) => r.unrealized_pct)).toEqual([
      "0.7",
      "0.5",
      "0.3",
      "0.1",
      "-0.2",
      "-0.4",
    ]);
    expect(bestWorst(rows.slice(0, 6)).ranked).toHaveLength(6);
  });

  it("orders equal percentages by name", () => {
    const rows = [pct("Beta", "0.1"), pct("Alfa", "0.10")];
    expect(bestWorst(rows).ranked.map((r) => r.name)).toEqual(["Alfa", "Beta"]);
  });
});

describe("largest (D-153 (3))", () => {
  it("shows the 10 largest and keeps the rest apart", () => {
    const parts = Array.from({ length: 12 }, (_, i) => ({ asset_id: `a${i}` }));
    const { shown, rest } = largest(parts);
    expect(shown.map((p) => p.asset_id)).toEqual(parts.slice(0, 10).map((p) => p.asset_id));
    expect(rest.map((p) => p.asset_id)).toEqual(["a10", "a11"]);
    expect(largest(parts.slice(0, 10)).rest).toEqual([]);
  });
});
