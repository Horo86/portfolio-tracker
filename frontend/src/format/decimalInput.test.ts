import { describe, expect, it } from "vitest";

import { decimalToInput, MAX_AMOUNT_DECIMALS, parseDecimalInput } from "./decimalInput";

const read = (text: string, locale: string, signed = false) => {
  const out = parseDecimalInput(text, locale, { signed });
  return out.kind === "ok" ? out.value : out.kind === "error" ? `!${out.problem}` : "";
};

describe("parseDecimalInput (D-140)", () => {
  it("always accepts the language's decimal separator", () => {
    expect(read("101,80", "it")).toBe("101.80");
    expect(read("101.80", "en")).toBe("101.80");
    expect(read("3,00", "it")).toBe("3.00"); // the scale typed is kept
    expect(read("15", "it")).toBe("15");
    expect(read(",5", "it")).toBe("0.5");
    expect(read("5,", "it")).toBe("5");
  });

  it("reads the other symbol as decimal when it cannot group thousands", () => {
    for (const [text, value] of [
      ["1.5", "1.5"],
      ["12.34", "12.34"],
      ["1.2345", "1.2345"],
      ["0.234", "0.234"],
      ["1234.567", "1234.567"],
    ]) {
      expect(read(text, "it"), text).toBe(value);
    }
    expect(read("1,5", "en")).toBe("1.5");
    expect(read("0,25", "en")).toBe("0.25");
  });

  it("reads one separator before exactly 3 digits as thousands, per the language", () => {
    expect(read("1.234", "it")).toBe("1234");
    expect(read("1,234", "it")).toBe("1.234");
    expect(read("1,234", "en")).toBe("1234");
    expect(read("1.234", "en")).toBe("1.234");
    expect(read("1.234.567", "it")).toBe("1234567");
    expect(read("1.234.567,89", "it")).toBe("1234567.89");
    expect(read("1,234,567.89", "en")).toBe("1234567.89");
  });

  it("accepts spaces, also Intl's no-break ones, between digits", () => {
    expect(read(" 1 234,5 ", "it")).toBe("1234.5");
    expect(read("1\u00a0234,5", "it")).toBe("1234.5");
    expect(read("1\u202f234.5", "en")).toBe("1234.5");
  });

  it("refuses what is not one number", () => {
    const wrong = ["1.2.3", "1,2,3", "1.23,4", "1,234.5", "12a", "1e5", ",", "-", "--1", "1-"];
    for (const text of wrong) {
      expect(read(text, "it"), text).toBe("!invalid");
    }
    expect(read("1,234.56", "it")).toBe("!invalid");
    expect(read("1.234,56", "en")).toBe("!invalid");
  });

  it("refuses more than 8 decimals and more than 15 integer digits", () => {
    expect(read("0,12345678", "it")).toBe("0.12345678");
    expect(read("0,123456789", "it")).toBe("!tooManyDecimals");
    expect(read("999999999999999", "en")).toBe("999999999999999");
    expect(read("1000000000000000", "en")).toBe("!tooLarge");
    expect(read("000000000000000001", "en")).toBe("1"); // leading zeros do not count
  });

  it("takes up to 20 decimals where the field allows them (prices and amounts, D-98)", () => {
    const amount = (text: string) => {
      const parsed = parseDecimalInput(text, "it", { maxDecimals: MAX_AMOUNT_DECIMALS });
      return parsed.kind === "ok" ? parsed.value : parsed.kind;
    };
    expect(amount("12,5679002040")).toBe("12.5679002040");
    expect(amount("0,12345678901234567890")).toBe("0.12345678901234567890");
    expect(amount("0,123456789012345678901")).toBe("error");
  });

  it("allows a minus sign only where the field is signed", () => {
    expect(read("-2,44", "it")).toBe("!negative");
    expect(read("-2,44", "it", true)).toBe("-2.44");
    expect(read("\u22122.44", "en", true)).toBe("-2.44");
    expect(read("+3", "en")).toBe("3");
    expect(read("-0", "en")).toBe("0");
    expect(read("-0,00", "it")).toBe("0.00");
  });

  it("keeps every digit of large values (no binary float)", () => {
    expect(read("123.456.789.012.345,12345678", "it")).toBe("123456789012345.12345678");
    expect(read("9007199254740993", "en")).toBe("!tooLarge"); // 16 digits
    expect(read("900719925474099,3", "it")).toBe("900719925474099.3");
  });

  it("says when nothing was typed", () => {
    expect(parseDecimalInput("  ", "it")).toEqual({ kind: "empty" });
  });
});

describe("decimalToInput", () => {
  it("writes an API value with the language's separator and every digit", () => {
    expect(decimalToInput("1234.50", "it")).toBe("1234,50");
    expect(decimalToInput("1234.50", "en")).toBe("1234.50");
    expect(decimalToInput("-2.44", "it")).toBe("-2,44");
    expect(decimalToInput("15", "it")).toBe("15");
  });

  it("round-trips through the parser", () => {
    for (const value of ["0.00000001", "123456789012345.12345678", "-7.5", "100"]) {
      for (const locale of ["it", "en"]) {
        expect(read(decimalToInput(value, locale), locale, true)).toBe(value);
      }
    }
  });
});
