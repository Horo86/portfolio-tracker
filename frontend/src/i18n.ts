import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import { readPreference } from "./storage";

export const LANGUAGES = ["it", "en"] as const;
export type Language = (typeof LANGUAGES)[number];
export const LANGUAGE_KEY = "pt.language";

// Each language's texts are a chunk of their own (D-138): the first screen loads only the
// active language; the other comes when the user switches to it.
const TEXTS: Record<Language, () => Promise<{ default: object }>> = {
  it: () => import("./locales/it.json"),
  en: () => import("./locales/en.json"),
};

function initialLanguage(): Language {
  const stored = readPreference(LANGUAGE_KEY);
  if (stored === "it" || stored === "en") return stored;
  return navigator.language.toLowerCase().startsWith("it") ? "it" : "en";
}

// Screen readers pronounce the page in the language of <html lang>.
i18n.on("languageChanged", (language) => {
  document.documentElement.lang = language;
});

/** Loads a language's texts, once. */
export async function loadLanguage(language: Language): Promise<void> {
  if (i18n.hasResourceBundle(language, "translation")) return;
  const { default: texts } = await TEXTS[language]();
  i18n.addResourceBundle(language, "translation", texts);
}

let wanted: Language | null = null;

/** Switches once the texts are loaded, so no screen shows untranslated keys; the last
 * language asked wins when two switches overlap. */
export async function changeLanguage(language: Language): Promise<void> {
  wanted = language;
  await loadLanguage(language);
  if (wanted === language && i18n.language !== language) await i18n.changeLanguage(language);
}

/** Settles once the first language's texts are loaded: the app renders after (main.tsx). */
export const i18nReady: Promise<void> = (async () => {
  const language = initialLanguage();
  const { default: texts } = await TEXTS[language]();
  await i18n.use(initReactI18next).init({
    resources: { [language]: { translation: texts } },
    partialBundledLanguages: true,
    lng: language,
    fallbackLng: "en",
    interpolation: { escapeValue: false },
  });
})();

export default i18n;
