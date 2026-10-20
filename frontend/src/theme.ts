import type { ThemeName } from "./api/types";
import { readPreference, writePreference } from "./storage";

// The four color themes (D-76, D-80); tokens in styles/themes.css.
export const THEMES: readonly ThemeName[] = ["sobrio", "moderno", "ambra", "grafite"];
export const DEFAULT_THEME: ThemeName = "moderno";
const THEME_KEY = "pt.theme";

export function isTheme(value: unknown): value is ThemeName {
  return typeof value === "string" && (THEMES as readonly string[]).includes(value);
}

/** The theme remembered in this browser (before login), else the default. */
export function storedTheme(): ThemeName {
  const value = readPreference(THEME_KEY);
  return isTheme(value) ? value : DEFAULT_THEME;
}

/** Shows the theme at once and remembers it in this browser. */
export function applyTheme(theme: ThemeName): void {
  document.documentElement.setAttribute("data-theme", theme);
  writePreference(THEME_KEY, theme);
}
