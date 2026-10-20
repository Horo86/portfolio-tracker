import { describe, expect, it } from "vitest";

import { passwordProblem } from "./validation";

describe("passwordProblem", () => {
  it("counts characters like the server, not UTF-16 units", () => {
    // 10 letters and an emoji: 11 characters, though the emoji is two UTF-16 units.
    expect(passwordProblem("abcdefghij😀")).toBe("validation.passwordMin");
    expect(passwordProblem("abcdefghijk😀")).toBeNull();
  });
});
