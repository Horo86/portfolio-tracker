import type { Language, Me } from "./api/types";
import i18n, { LANGUAGE_KEY } from "./i18n";
import { writePreference } from "./storage";
import { applyTheme } from "./theme";

// Before login: the browser's language and the stored preference. After login the user's
// settings (/me) win and update the stored preference (M5 planning, Q-7).

export function setLanguage(language: Language): void {
  writePreference(LANGUAGE_KEY, language);
  if (i18n.language !== language) void i18n.changeLanguage(language);
}

export function syncPreferences(me: Pick<Me, "language" | "theme">): void {
  setLanguage(me.language);
  applyTheme(me.theme);
}
