import { describe, expect, it } from "vitest";

import { formatDay, formatMoment, formatMomentDay, formatMonth } from "./date";

describe("formatDay", () => {
  it("follows the language and never shifts the day", () => {
    expect(formatDay("2026-10-18", "it")).toBe("18 ott 2026");
    expect(formatDay("2026-10-18", "en")).toBe("Oct 18, 2026");
    expect(formatDay("2026-01-01", "en")).toBe("Jan 1, 2026");
  });
});

describe("formatMoment", () => {
  it("shows the day and the time", () => {
    // Midday UTC is the same day in every time zone the tests may run in.
    expect(formatMoment("2026-10-18T12:00:00Z", "en")).toMatch(/^Oct 18, 2026, \d{1,2}:\d{2}/);
    expect(formatMoment("2026-10-18T12:00:00+00:00", "it")).toMatch(/^18 ott 2026, \d{2}:\d{2}$/);
    expect(formatMomentDay("2026-10-18T12:00:00Z", "en")).toBe("Oct 18, 2026");
  });
});

describe("formatMonth", () => {
  it("names a month of the API (YYYY-MM) in the language, the same in every time zone", () => {
    expect(formatMonth("2026-10", "it")).toBe("ott 2026");
    expect(formatMonth("2026-01", "en")).toBe("Jan 2026");
    expect(formatMonth("2025-12", "en")).toBe("Dec 2025");
  });
});
