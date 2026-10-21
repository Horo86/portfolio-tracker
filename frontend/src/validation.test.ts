import { describe, expect, it } from "vitest";

import { isinProblem, normalIsin, passwordProblem } from "./validation";

describe("passwordProblem", () => {
  it("counts characters like the server, not UTF-16 units", () => {
    // 10 letters and an emoji: 11 characters, though the emoji is two UTF-16 units.
    expect(passwordProblem("abcdefghij😀")).toBe("validation.passwordMin");
    expect(passwordProblem("abcdefghijk😀")).toBeNull();
  });
});

describe("isinProblem", () => {
  // Invented codes: "ZZ" is not a country (the development data uses the same kind).
  it("accepts a valid code and names a wrong format or check digit, like the server", () => {
    expect(isinProblem("ZZ0000002012")).toBeNull();
    expect(isinProblem("ZZ0000002020")).toBeNull();
    expect(isinProblem("ZZ0000002013")).toBe("validation.isinCheck");
    expect(isinProblem("ZZ000000201")).toBe("validation.isinFormat");
    expect(isinProblem("1Z0000002012")).toBe("validation.isinFormat");
  });

  it("reads the code as the server does: trimmed, in capitals", () => {
    expect(normalIsin(" zz0000002012 ")).toBe("ZZ0000002012");
  });
});
