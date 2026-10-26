import { describe, expect, it } from "vitest";

import i18n from "./i18n";
import { asksForManualPrice, STATUS_CODES, staleKey, statusCode, statusTone } from "./priceStatus";
import { WORLD_ETF } from "./test/holdings";

const facts = (price_status: string, price_status_detail: string | null = null, isin = "ZZ1") => ({
  ...WORLD_ETF,
  isin,
  price_status,
  price_status_detail,
});

describe("price status (D-144, D-145, ui README appendix)", () => {
  it("maps every status and detail of the API to a code", () => {
    expect(statusCode(facts("OK"), "ON")).toBe("OK");
    expect(statusCode(facts("PENDING"), "ON")).toBe("PENDING");
    expect(statusCode(facts("NOT_AVAILABLE"), "ON")).toBe("NOT_AVAILABLE");
    expect(statusCode(facts("MANUAL", null, ""), "ON")).toBe("MANUAL");
    expect(statusCode(facts("MISMATCH", "CURRENCY"), "ON")).toBe("MISMATCH_CURRENCY");
    expect(statusCode(facts("MISMATCH", "UNIT"), "ON")).toBe("MISMATCH_UNIT");
    expect(statusCode(facts("MISMATCH", "LEVEL"), "ON")).toBe("MISMATCH_LEVEL");
    expect(statusCode(facts("MISMATCH", "SOMETHING_NEW"), "ON")).toBe("MISMATCH");
    expect(statusCode(facts("FAILING"), "ON")).toBe("FAILING");
    expect(statusCode(facts("FAILING", "NOT_LISTED"), "ON")).toBe("NOT_LISTED");
    expect(statusCode(facts("A_NEWER_STATUS"), "ON")).toBeNull();
  });

  it("with market data off, an asset with an ISIN says so instead of its status", () => {
    for (const status of ["OK", "PENDING", "NOT_AVAILABLE", "MISMATCH", "FAILING"]) {
      expect(statusCode(facts(status), "OFF")).toBe("OFF");
    }
    expect(statusCode({ ...facts("MANUAL"), isin: null }, "OFF")).toBe("MANUAL");
  });

  it("warns where the user must act; the rest is information", () => {
    expect(statusTone("OK")).toBe("ok");
    for (const code of ["PENDING", "MANUAL", "OFF"] as const) expect(statusTone(code)).toBe("info");
    for (const code of [
      "NOT_AVAILABLE",
      "MISMATCH",
      "MISMATCH_CURRENCY",
      "MISMATCH_UNIT",
      "MISMATCH_LEVEL",
      "FAILING",
      "NOT_LISTED",
    ] as const) {
      expect(statusTone(code)).toBe("warning");
    }
  });

  it("links to the manual prices where the sentence asks for one, or the price is stale (D-149)", () => {
    const asks = STATUS_CODES.filter((code) => asksForManualPrice(WORLD_ETF, code));
    expect(asks).toEqual([
      "NOT_AVAILABLE",
      "MANUAL",
      "MISMATCH",
      "MISMATCH_CURRENCY",
      "MISMATCH_UNIT",
      "NOT_LISTED",
      "OFF",
    ]);
    const stale = { ...WORLD_ETF, price_stale: true };
    for (const code of ["OK", "PENDING", "FAILING", "MISMATCH_LEVEL"] as const) {
      expect(asksForManualPrice(stale, code), code).toBe(true);
    }
    expect(asksForManualPrice(stale, null)).toBe(true);
    expect(asksForManualPrice(WORLD_ETF, null)).toBe(false);
  });

  it("promises the next daily update only where one can bring a price", () => {
    const stale = { ...WORLD_ETF, price_stale: true };
    expect(staleKey(stale, "OK")).toBe("priceStatus.staleNext");
    expect(staleKey(stale, "FAILING")).toBe("priceStatus.staleNext");
    expect(staleKey(stale, "PENDING")).toBe("priceStatus.staleNext");
    for (const code of ["OFF", "MANUAL", "NOT_AVAILABLE", "NOT_LISTED", "MISMATCH_UNIT"] as const) {
      expect(staleKey(stale, code)).toBe("priceStatus.stale");
    }
    expect(staleKey(WORLD_ETF, "OK")).toBeNull();
    expect(staleKey({ ...stale, price_date: null }, "OK")).toBeNull();
  });

  it("has a short and a long text for every code in both languages", () => {
    for (const language of ["it", "en"]) {
      for (const code of STATUS_CODES) {
        for (const form of ["short", "long"]) {
          const key = `priceStatus.${form}.${code}`;
          expect(i18n.exists(key, { lng: language }), `${language} ${key}`).toBe(true);
        }
      }
    }
  });
});
