import { describe, expect, it } from "vitest";

import i18n, { changeLanguage } from "./i18n";

describe("language switch", () => {
  it("switches only once the texts are loaded", async () => {
    i18n.removeResourceBundle("it", "translation");
    const switching = changeLanguage("it");
    expect(i18n.language).toBe("en"); // still English while the Italian texts load
    await switching;
    expect(i18n.language).toBe("it");
    expect(i18n.t("common.cancel")).toBe("Annulla");
  });

  it("keeps the last language asked when two switches overlap", async () => {
    i18n.removeResourceBundle("it", "translation");
    await Promise.all([changeLanguage("it"), changeLanguage("en")]);
    expect(i18n.language).toBe("en");
  });
});
