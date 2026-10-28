import { describe, expect, it } from "vitest";

import { dayToInput, isApiDay, notADay, parseDayInput } from "./dateInput";

// Both languages write the day first (gg/mm/aaaa, dd/mm/yyyy): reading and writing a day do
// not depend on the language, only the texts around the field do.
const read = (text: string) => {
  const out = parseDayInput(text);
  return out.kind === "ok" ? out.value : out.kind === "error" ? `!${out.problem}` : "";
};

describe("parseDayInput", () => {
  it("reads day, month and a four-digit year into the API's YYYY-MM-DD", () => {
    expect(read("28/10/2026")).toBe("2026-10-28");
    expect(read("01/01/2026")).toBe("2026-01-01");
    expect(read("31/12/1999")).toBe("1999-12-31");
  });

  it("accepts a slash, a dash or a dot between the parts", () => {
    for (const text of ["5/3/2026", "5-3-2026", "5.3.2026", "05/03/2026", "05.03.2026"]) {
      expect(read(text), text).toBe("2026-03-05");
    }
  });

  it("accepts a one-digit day or month, and spaces around the date", () => {
    expect(read("1/2/2026")).toBe("2026-02-01");
    expect(read("1/12/2026")).toBe("2026-12-01");
    expect(read("12/1/2026")).toBe("2026-01-12");
    expect(read("  7/7/2026 ")).toBe("2026-07-07");
  });

  it("gives nothing for an empty field", () => {
    expect(read("")).toBe("");
    expect(read("   ")).toBe("");
  });

  it("refuses two-digit years and any other shape", () => {
    for (const text of [
      "28/10/26",
      "28/10/026",
      "28/10/20266",
      "2026-10-28", // the API's order is not the user's
      "28/10",
      "28 10 2026",
      "28/10-2026",
      "a/b/cccc",
      "123/1/2026",
      "28//10/2026",
      "28/10/0999",
    ]) {
      expect(read(text), text).toBe("!format");
    }
  });

  it("refuses days that do not exist", () => {
    for (const text of [
      "31/02/2026",
      "30/02/2024",
      "29/02/2026",
      "31/04/2026",
      "0/1/2026",
      "1/0/2026",
      "1/13/2026",
      "32/1/2026",
    ]) {
      expect(read(text), text).toBe("!impossible");
    }
  });

  it("knows the leap day: every 4 years, not every 100, again every 400", () => {
    expect(read("29/02/2024")).toBe("2024-02-29");
    expect(read("29/02/2000")).toBe("2000-02-29");
    expect(read("29/02/1900")).toBe("!impossible");
    expect(read("29/02/2100")).toBe("!impossible");
  });
});

describe("dayToInput", () => {
  it("writes an API day as dd/mm/yyyy", () => {
    expect(dayToInput("2026-10-28")).toBe("28/10/2026");
    expect(dayToInput("2026-03-05")).toBe("05/03/2026");
  });

  it("gives back what the user typed for a text that is not a day, and nothing for nothing", () => {
    expect(dayToInput("")).toBe("");
    expect(dayToInput(notADay("31/02/2026"))).toBe("31/02/2026");
  });

  it("never lets a text typed in the API's order pass as a day", () => {
    expect(notADay("2026-10-28")).not.toBe("2026-10-28");
    expect(isApiDay(notADay("2026-10-28"))).toBe(false);
    expect(isApiDay("2026-10-28")).toBe(true);
  });

  it("reads back what it writes", () => {
    for (const day of ["2026-10-28", "2024-02-29", "1999-01-01"]) {
      expect(read(dayToInput(day))).toBe(day);
    }
  });
});
