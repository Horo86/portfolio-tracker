import type { ScreenTexts } from "./i18n";

// The texts of the read-only "Price sources" card of the administration area (D-175 (4)):
// administrators only, so a chunk of their own per language, off the first screen (D-138).
export const PRICE_SOURCES_TEXTS: ScreenTexts = {
  it: () => import("./locales/priceSources.it.json"),
  en: () => import("./locales/priceSources.en.json"),
};
