import type { ScreenTexts } from "./i18n";

// The texts of the "Security" card of the administration area (D-182): administrators only,
// so a chunk of their own per language, off the first screen (D-138).
export const SECURITY_SETTINGS_TEXTS: ScreenTexts = {
  it: () => import("./locales/securitySettings.it.json"),
  en: () => import("./locales/securitySettings.en.json"),
};
