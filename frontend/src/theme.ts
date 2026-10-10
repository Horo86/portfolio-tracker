import { readPreference, writePreference } from "./storage";

// Placeholder for the theme switch (M0): light, dark or follow the system setting.
export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];
const THEME_KEY = "pt.theme";

export function storedTheme(): Theme {
  const value = readPreference(THEME_KEY);
  return value === "light" || value === "dark" ? value : "system";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  writePreference(THEME_KEY, theme);
}
