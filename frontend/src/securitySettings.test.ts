import { describe, expect, it } from "vitest";

import type { SecuritySettings } from "./api/types";
import { changesOf, draftsOf, isChanged, parseWhole, problemsOf, valuesOf } from "./securitySettings";

const VALUES: SecuritySettings["settings"] = {
  session_idle_days: 7,
  session_absolute_days: 30,
  login_account_failures: 5,
  login_address_failures: 30,
  block_first_minutes: 1,
  block_max_minutes: 15,
  require_2fa_all_users: false,
};
const BOUNDS: SecuritySettings["bounds"] = {
  session_idle_days: { min: 1, max: 30 },
  session_absolute_days: { min: 1, max: 90 },
  login_account_failures: { min: 3, max: 20 },
  login_address_failures: { min: 10, max: 200 },
  block_first_minutes: { min: 1, max: 15 },
  block_max_minutes: { min: 15, max: 120 },
};

describe("parseWhole", () => {
  it.each([
    ["7", 7],
    [" 12 ", 12],
    ["007", 7],
  ])("reads %j", (text, value) => expect(parseWhole(text)).toBe(value));

  it.each(["", " ", "7.5", "7,5", "-1", "+3", "1e2", "seven", "1234567"])(
    "refuses %j",
    (text) => expect(parseWhole(text)).toBeNull(),
  );
});

describe("problemsOf", () => {
  it("finds nothing in the defaults", () => {
    expect(problemsOf(draftsOf(VALUES), BOUNDS)).toEqual({});
  });

  it("says a field is not a whole number, or out of its range", () => {
    const drafts = { ...draftsOf(VALUES), session_idle_days: "x", block_max_minutes: "121" };
    expect(problemsOf(drafts, BOUNDS)).toEqual({
      session_idle_days: { key: "securitySettings.errors.whole" },
      block_max_minutes: {
        key: "securitySettings.errors.range",
        values: { min: 15, max: 120 },
      },
    });
  });

  it("checks idle at most absolute, and per address at least twice per user", () => {
    const drafts = {
      ...draftsOf(VALUES),
      session_idle_days: "9",
      session_absolute_days: "8",
      login_account_failures: "10",
      login_address_failures: "19",
    };
    expect(problemsOf(drafts, BOUNDS)).toEqual({
      session_idle_days: { key: "securitySettings.errors.idleOverAbsolute", values: { max: 8 } },
      login_address_failures: {
        key: "securitySettings.errors.addressUnderAccount",
        values: { min: 20 },
      },
    });
  });

  it("leaves the cross rules to valid fields only", () => {
    const drafts = { ...draftsOf(VALUES), session_absolute_days: "x", login_account_failures: "99" };
    const found = problemsOf(drafts, BOUNDS);
    expect(Object.keys(found).sort()).toEqual(["login_account_failures", "session_absolute_days"]);
  });

  it("takes the bounds from the server's answer", () => {
    const bounds = { ...BOUNDS, session_idle_days: { min: 2, max: 5 } };
    expect(problemsOf({ ...draftsOf(VALUES), session_idle_days: "1" }, bounds)).toEqual({
      session_idle_days: { key: "securitySettings.errors.range", values: { min: 2, max: 5 } },
    });
  });
});

describe("the changes", () => {
  it("lists only what differs, in the form's order", () => {
    const drafts = { ...draftsOf(VALUES), block_max_minutes: "60", session_idle_days: " 8" };
    expect(changesOf(drafts, VALUES)).toEqual([
      { name: "session_idle_days", from: 7, to: 8 },
      { name: "block_max_minutes", from: 15, to: 60 },
    ]);
    expect(valuesOf(drafts)).toEqual({ ...VALUES, session_idle_days: 8, block_max_minutes: 60 });
  });

  it("is changed when any text differs from the value in force", () => {
    expect(isChanged(draftsOf(VALUES), VALUES)).toBe(false);
    expect(isChanged({ ...draftsOf(VALUES), session_idle_days: " 7 " }, VALUES)).toBe(false);
    expect(isChanged({ ...draftsOf(VALUES), session_idle_days: "" }, VALUES)).toBe(true);
  });
});

describe("the obligation of two-factor sign-in for everyone (D-183)", () => {
  it("is a change of its own, after the numbers, and is sent as it is", () => {
    const drafts = { ...draftsOf(VALUES), require_2fa_all_users: true, block_first_minutes: "2" };
    expect(isChanged(drafts, VALUES)).toBe(true);
    expect(changesOf(drafts, VALUES)).toEqual([
      { name: "block_first_minutes", from: 1, to: 2 },
      { name: "require_2fa_all_users", from: false, to: true },
    ]);
    expect(valuesOf(drafts).require_2fa_all_users).toBe(true);
    expect(problemsOf(drafts, BOUNDS)).toEqual({});
  });

  it("alone makes the form changed", () => {
    expect(isChanged({ ...draftsOf(VALUES), require_2fa_all_users: true }, VALUES)).toBe(true);
  });
});
