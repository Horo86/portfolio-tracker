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
 * language asked wins when two switches overlap. Texts that cannot be loaded (e.g. in a tab
 * opened before an update, whose chunks are gone) leave the current language as it is. */
export async function changeLanguage(language: Language): Promise<void> {
  wanted = language;
  try {
    await loadLanguage(language);
  } catch {
    return;
  }
  if (wanted === language && i18n.language !== language) await i18n.changeLanguage(language);
}

/** The texts of `language`, or of the other language when they cannot be loaded. */
async function firstTexts(language: Language): Promise<{ language: Language; texts: object }> {
  const order = [language, ...LANGUAGES.filter((other) => other !== language)];
  for (const candidate of order) {
    try {
      return { language: candidate, texts: (await TEXTS[candidate]()).default };
    } catch {
      // try the next language
    }
  }
  return { language, texts: {} }; // none: the keys show, but the app still starts
}

/** Settles once the first language's texts are loaded: the app renders after (main.tsx).
 * i18next is set up even when they cannot be loaded. */
export const i18nReady: Promise<void> = (async () => {
  const { language, texts } = await firstTexts(initialLanguage());
  await i18n.use(initReactI18next).init({
    resources: { [language]: { translation: texts } },
    partialBundledLanguages: true,
    lng: language,
    fallbackLng: "en",
    interpolation: { escapeValue: false },
  });
})();

export default i18n;
