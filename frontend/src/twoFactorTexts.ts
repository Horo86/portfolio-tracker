import type { ScreenTexts } from "./i18n";

// The texts of the enrolment and of the two-factor parts of Settings and the Users screen
// (D-174): not needed by the first screen, so a chunk of their own per language (D-138). The
// login's second step uses the first screen's texts.
export const TWO_FACTOR_TEXTS: ScreenTexts = {
  it: () => import("./locales/twoFactor.it.json"),
  en: () => import("./locales/twoFactor.en.json"),
};
