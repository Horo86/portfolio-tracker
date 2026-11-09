import { describe, expect, it } from "vitest";

import {
  emailProblem,
  isinProblem,
  nameProblem,
  normalEmail,
  normalIsin,
  normalName,
  normalUsername,
  passwordProblem,
  usernameProblem,
} from "./validation";

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

// The same cases as backend/tests/test_identity.py (D-173): the client says what the API refuses.
describe("usernameProblem", () => {
  const ok = ["mario", "Mario.Rossi", "ana_maría", "user-01", "a.b@example.org", "x+tag@example.org",
    "Ελένη", "Ñandú", "żółw", "李小龙", "abc", "a".repeat(64), "e\u0301".repeat(64)];
  const refused = ["ab", "a".repeat(65), "mario rossi", "mario\trossi", "mario!", "mario#1",
    "mario/1", "ma'rio", "\u0661\u0662\u0663abc", "\u0301abc", "", "   "];
  it.each(ok)("accepts %s", (name) => expect(usernameProblem(normalUsername(name))).toBeNull());
  it.each(refused)("refuses %s", (name) => {
    expect(usernameProblem(normalUsername(name))).toBe("validation.usernamePattern");
  });
  it("keeps the username as typed, in NFC, without surrounding spaces", () => {
    expect(normalUsername("  A\u0300lex ")).toBe("Àlex");
  });
});

describe("nameProblem", () => {
  it.each([
    ["  Mario  ", "Mario"],
    ["Anna   Maria", "Anna Maria"],
    ["D'Angelo", "D'Angelo"],
    ["O\u2019Brien", "O\u2019Brien"],
    ["Jean-Luc", "Jean-Luc"],
    ["Zoe\u0308", "Zoë"],
    ["Ελένη", "Ελένη"],
    ["x".repeat(100), "x".repeat(100)],
  ])("accepts %s as %s", (typed, stored) => {
    expect(normalName(typed)).toBe(stored);
    expect(nameProblem(stored)).toBeNull();
  });
  it.each(["Mario2", "-Mario", "'Mario", "Mario!", "Mario_Rossi", "x".repeat(101)])(
    "refuses %s",
    (name) => expect(nameProblem(normalName(name))).toBe("validation.namePattern"),
  );
  it("asks for an empty one", () => expect(nameProblem(normalName("   "))).toBe("validation.required"));
});

describe("emailProblem", () => {
  it.each([
    [" Mario.Rossi@Example.ORG ", "mario.rossi@example.org"],
    ["x+tag@mail.example.org", "x+tag@mail.example.org"],
    ["a_b-c@sub-domain.example.org", "a_b-c@sub-domain.example.org"],
  ])("accepts %s as %s", (typed, stored) => {
    expect(normalEmail(typed)).toBe(stored);
    expect(emailProblem(stored)).toBeNull();
  });
  it.each([
    "mario", "mario@", "@example.org", "mario@example", "mario@@example.org", "ma rio@example.org",
    "mario@exa mple.org", ".mario@example.org", "mario.@example.org", "ma..rio@example.org",
    "mario@-example.org", "mario@example-.org", "mario@example..org", "màrio@example.org",
    "mario@exämple.org", '"mario"@example.org', "mario@example.org.", `${"a".repeat(65)}@example.org`,
    `a@${"b".repeat(250)}.org`,
  ])("refuses %s", (email) => expect(emailProblem(normalEmail(email))).toBe("validation.emailPattern"));
  it("asks for an empty one", () => expect(emailProblem(normalEmail(" "))).toBe("validation.required"));
});
