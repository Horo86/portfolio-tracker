import type { TFunction } from "i18next";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { errorText, KNOWN_ERROR_CODES } from "../errors";
import i18n from "../i18n";
import en from "./en.json";
import it_ from "./it.json";

function keys(node: unknown, prefix = ""): string[] {
  if (node === null || typeof node !== "object") return [prefix];
  return Object.entries(node).flatMap(([key, value]) =>
    keys(value, prefix ? `${prefix}.${key}` : key),
  );
}

describe("translation files", () => {
  it("have the same keys in Italian and English", () => {
    expect(keys(it_).sort()).toEqual(keys(en).sort());
  });

  it("have no empty text", () => {
    for (const [name, file] of [["it", it_], ["en", en]] as const) {
      for (const key of keys(file)) {
        const value = key.split(".").reduce<unknown>((node, part) => (node as never)[part], file);
        expect(typeof value === "string" && value.trim() !== "", `${name}: ${key}`).toBe(true);
      }
    }
  });

  it("have a text for every known error code", () => {
    const errorKeys = new Set(Object.keys(en.errors));
    for (const code of KNOWN_ERROR_CODES) expect(errorKeys, code).toContain(code);
  });
});

describe("errorText", () => {
  const t = i18n.getFixedT("en") as TFunction;

  it("translates the code, never the server detail", () => {
    expect(errorText(t, new ApiError(401, "INVALID_CREDENTIALS"))).toBe(
      "Wrong username or password.",
    );
  });

  it("names an unknown code and survives non-API errors", () => {
    expect(errorText(t, new ApiError(409, "SOMETHING_NEW"))).toBe(
      "Something went wrong (code SOMETHING_NEW).",
    );
    expect(errorText(t, new Error("boom"))).toBe("Something went wrong (code ?).");
  });

  it("tells how long to wait after too many attempts", () => {
    expect(errorText(t, new ApiError(429, "TOO_MANY_ATTEMPTS", {}, 60))).toBe(
      "Too many attempts. Try again in 1 minute.",
    );
    expect(errorText(t, new ApiError(429, "TOO_MANY_ATTEMPTS", {}, 61))).toBe(
      "Too many attempts. Try again in 2 minutes.",
    );
    expect(errorText(t, new ApiError(429, "TOO_MANY_ATTEMPTS"))).toBe(
      "Too many attempts. Try again later.",
    );
    const tIt = i18n.getFixedT("it") as TFunction;
    expect(errorText(tIt, new ApiError(429, "TOO_MANY_ATTEMPTS", {}, 900))).toBe(
      "Troppi tentativi. Riprova tra 15 minuti.",
    );
  });
});
