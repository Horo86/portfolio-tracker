import { describe, expect, it } from "vitest";

import {
  compareDecimal,
  formatDecimal,
  formatMoney,
  formatPercent,
  formatPrice,
  formatQuantity,
  signOf,
} from "./decimal";

// Intl puts a no-break space between the amount and the euro sign in Italian.
const nbsp = (text: string) => text.replace(/\u00a0/g, " ");

describe("formatMoney", () => {
  it("groups from four digits and follows the language", () => {
    expect(nbsp(formatMoney("21368.05", "EUR", "it"))).toBe("21.368,05 €");
    expect(nbsp(formatMoney("1240.1", "EUR", "it"))).toBe("1.240,10 €");
    expect(formatMoney("21368.05", "EUR", "en")).toBe("€21,368.05");
    expect(nbsp(formatMoney("8420", "USD", "it"))).toBe("8.420,00 $");
    expect(formatMoney("8420", "USD", "en")).toBe("$8,420.00");
    expect(formatMoney("0.5", "EUR", "en")).toBe("€0.50");
  });

  it("puts the sign where the language does; gains carry + and zero none", () => {
    expect(nbsp(formatMoney("-1234.5", "EUR", "it"))).toBe("-1.234,50 €");
    expect(formatMoney("-1234.5", "EUR", "en")).toBe("-€1,234.50");
    expect(formatMoney("4980.22", "EUR", "en", { signed: true })).toBe("+€4,980.22");
    expect(nbsp(formatMoney("4980.22", "EUR", "it", { signed: true }))).toBe("+4.980,22 €");
    expect(formatMoney("0.00", "EUR", "en", { signed: true })).toBe("€0.00");
  });

  it("rounds half away from zero for display (D-137)", () => {
    expect(formatMoney("0.125", "EUR", "en")).toBe("€0.13");
    expect(formatMoney("0.135", "EUR", "en")).toBe("€0.14");
    expect(formatMoney("-0.125", "EUR", "en")).toBe("-€0.13");
    expect(formatMoney("0.12499999", "EUR", "en")).toBe("€0.12");
    expect(formatMoney("999.995", "EUR", "en")).toBe("€1,000.00");
    expect(formatMoney("-0.004", "EUR", "en")).toBe("€0.00");
  });

  it("keeps every digit of large values (no binary float)", () => {
    // 2^53 + 1 cannot be represented as a float.
    expect(formatMoney("9007199254740993.01", "EUR", "en")).toBe("€9,007,199,254,740,993.01");
    expect(formatMoney("999999999999999.99", "EUR", "en")).toBe("€999,999,999,999,999.99");
  });
});

describe("formatQuantity", () => {
  it("shows up to 8 decimals without trailing zeros", () => {
    expect(formatQuantity("12.34567891", "en")).toBe("12.34567891");
    expect(formatQuantity("12.345678915", "en")).toBe("12.34567892");
    expect(formatQuantity("5000.00000000", "it")).toBe("5.000");
    expect(formatQuantity("0.00000001", "it")).toBe("0,00000001");
    expect(formatQuantity("-3.5", "en")).toBe("-3.5");
  });
});

describe("formatDecimal", () => {
  it("rejects anything that is not a plain decimal string", () => {
    for (const value of ["", "1e5", "1,5", "abc", "NaN", "1.2.3"]) {
      expect(() => formatDecimal(value, "en", { minDecimals: 0, maxDecimals: 2 })).toThrow();
    }
  });
});

describe("signOf", () => {
  it("reads the sign from the string; any zero has none", () => {
    expect(signOf("4980.22")).toBe(1);
    expect(signOf("-0.01")).toBe(-1);
    expect(signOf("0.00")).toBe(0);
    expect(signOf("-0")).toBe(0);
  });
});

describe("compareDecimal", () => {
  it("orders decimal strings by value, never through a binary float", () => {
    expect(compareDecimal("0.1", "0.09")).toBe(1);
    expect(compareDecimal("-0.5", "0.01")).toBe(-1);
    expect(compareDecimal("-0.5", "-0.05")).toBe(-1);
    expect(compareDecimal("2.50", "2.5")).toBe(0);
    expect(compareDecimal("0", "-0.00")).toBe(0);
    expect(compareDecimal("10", "9.99999999")).toBe(1);
    expect(compareDecimal("-10", "-9.5")).toBe(-1);
    expect(compareDecimal("007", "10")).toBe(-1);
    // Past a float's precision: 17 significant digits that differ only in the last one.
    expect(compareDecimal("1234567890.12345679", "1234567890.12345678")).toBe(1);
  });
});

describe("formatPrice", () => {
  it("shows 2 to 4 decimals (domain-rules §0)", () => {
    expect(formatPrice("101.8", "en", "EUR")).toBe("€101.80");
    expect(formatPrice("100.05333333", "en", "EUR")).toBe("€100.0533");
    expect(formatPrice("98.25", "it")).toBe("98,25");
    expect(formatPrice("1234.56785", "it")).toBe("1.234,5679");
  });
});

describe("formatPercent", () => {
  it("shows a ratio from the API as a percentage with 2 decimals, per language (D-146)", () => {
    expect(formatPercent("0.08345827", "en")).toBe("8.35%");
    expect(formatPercent("0.08345827", "it")).toBe("8,35%"); // CLDR: no space in Italian
    expect(formatPercent("1", "en")).toBe("100.00%");
    expect(formatPercent("12.5", "en")).toBe("1,250.00%");
    expect(formatPercent("0", "en")).toBe("0.00%");
  });

  it("signs gains on request and rounds half away from zero on the string (D-137)", () => {
    expect(formatPercent("0.08345827", "en", { signed: true })).toBe("+8.35%");
    expect(formatPercent("-0.01922597", "en", { signed: true })).toBe("-1.92%");
    expect(formatPercent("-0.01922597", "it", { signed: true })).toBe("-1,92%");
    expect(formatPercent("0.00005", "en")).toBe("0.01%");
    expect(formatPercent("-0.00005", "en")).toBe("-0.01%");
    expect(formatPercent("0.0000049", "en", { signed: true })).toBe("0.00%"); // no sign on zero
    expect(formatPercent("0.1", "en")).toBe("10.00%");
  });
});
