import { describe, expect, it } from "vitest";

import { formatDecimal, formatMoney, formatQuantity } from "./decimal";

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
