import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import it from "./locales/it.json";
import { readPreference } from "./storage";

export const LANGUAGES = ["it", "en"] as const;
export type Language = (typeof LANGUAGES)[number];
export const LANGUAGE_KEY = "pt.language";

function initialLanguage(): Language {
  const stored = readPreference(LANGUAGE_KEY);
  if (stored === "it" || stored === "en") return stored;
  return navigator.language.toLowerCase().startsWith("it") ? "it" : "en";
}

// Screen readers pronounce the page in the language of <html lang>.
i18n.on("languageChanged", (language) => {
  document.documentElement.lang = language;
});

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, it: { translation: it } },
  lng: initialLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export default i18n;
