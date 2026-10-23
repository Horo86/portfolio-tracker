import { createInstance, type TFunction } from "i18next";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { errorText, issueText, KNOWN_ERROR_CODES, KNOWN_ISSUE_CODES } from "../errors";
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

  it("have a text for every known field issue code", () => {
    const issueKeys = new Set(Object.keys(en.issues));
    for (const code of KNOWN_ISSUE_CODES) expect(issueKeys, code).toContain(code);
  });
});

describe("issueText", () => {
  it("translates a known issue and names an unknown one", () => {
    const t = i18n.getFixedT("en") as TFunction;
    expect(issueText(t, "must_be_positive")).toBe("Enter a number greater than zero.");
    expect(issueText(t, "something_new")).toBe("Not valid (code something_new).");
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

  it("uses the text of a code missing from the known list", async () => {
    const other = createInstance();
    await other.init({
      lng: "en",
      resources: {
        en: {
          translation: {
            errors: { UNKNOWN: "Unknown {{code}}.", IMPORT_EXAMPLE_NEW: "An example text." },
          },
        },
      },
    });
    expect(errorText(other.t, new ApiError(422, "IMPORT_EXAMPLE_NEW"))).toBe("An example text.");
    expect(errorText(t, new ApiError(422, "retryLater"))).toBe(
      "Something went wrong (code retryLater).",
    );
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
