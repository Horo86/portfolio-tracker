import { useSyncExternalStore } from "react";

import type { ThemeName } from "./api/types";
import { readPreference, writePreference } from "./storage";

// The four color themes (D-76, D-80); tokens in styles/themes.css.
export const THEMES: readonly ThemeName[] = ["sobrio", "moderno", "ambra", "grafite"];
export const DEFAULT_THEME: ThemeName = "moderno";
const THEME_KEY = "pt.theme";

// Light or dark (D-176): per browser, not the user's setting; dark is one palette shown
// instead of the colour theme, never a value of the user's theme.
export type Appearance = "system" | "light" | "dark";
export const APPEARANCES: readonly Appearance[] = ["system", "light", "dark"];
export const DARK_THEME = "scuro";
export type ShownTheme = ThemeName | typeof DARK_THEME;
const APPEARANCE_KEY = "pt.appearance";
const DARK_QUERY = "(prefers-color-scheme: dark)";

export function isTheme(value: unknown): value is ThemeName {
  return typeof value === "string" && (THEMES as readonly string[]).includes(value);
}

/** The theme remembered in this browser (before login), else the default. */
export function storedTheme(): ThemeName {
  const value = readPreference(THEME_KEY);
  return isTheme(value) ? value : DEFAULT_THEME;
}

/** Light or dark as chosen in this browser; missing or unknown is "same as the device". */
export function storedAppearance(): Appearance {
  const value = readPreference(APPEARANCE_KEY);
  return (APPEARANCES as readonly (string | null)[]).includes(value)
    ? (value as Appearance)
    : "system";
}

/** Whether the device asks for dark; a browser without matchMedia (and jsdom) stays light. */
export function systemDark(): boolean {
  try {
    return window.matchMedia?.(DARK_QUERY)?.matches ?? false;
  } catch {
    return false;
  }
}

/** The theme on screen: dark when chosen, or when the device asks for it; else the colours. */
export function shownTheme(
  theme: ThemeName,
  appearance: Appearance = storedAppearance(),
  dark: boolean = systemDark(),
): ShownTheme {
  const isDark = appearance === "dark" || (appearance === "system" && dark);
  return isDark ? DARK_THEME : theme;
}

// The user's colour theme last applied, and who follows what is on screen.
let colourTheme: ThemeName | null = null;
const listeners = new Set<() => void>();

function show(): void {
  const shown = shownTheme(colourTheme ?? storedTheme());
  document.documentElement.setAttribute("data-theme", shown);
  for (const listener of listeners) listener();
}

/** Shows the theme at once (or dark, when it applies) and remembers it in this browser. */
export function applyTheme(theme: ThemeName): void {
  colourTheme = theme;
  writePreference(THEME_KEY, theme);
  show();
}

/** Shows light or dark at once and remembers the choice in this browser. */
export function applyAppearance(appearance: Appearance): void {
  writePreference(APPEARANCE_KEY, appearance);
  show();
}

/** Follows the device's light or dark setting live; returns the function that stops it. */
export function watchSystem(): () => void {
  let query: MediaQueryList | undefined;
  try {
    query = window.matchMedia?.(DARK_QUERY);
  } catch {
    query = undefined;
  }
  if (!query?.addEventListener) return () => {};
  query.addEventListener("change", show);
  return () => query.removeEventListener("change", show);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const shownNow = () => document.documentElement.getAttribute("data-theme") ?? undefined;

/** The theme on screen, followed live (for the charts, which read its tokens). */
export function useShownTheme(): string | undefined {
  return useSyncExternalStore(subscribe, shownNow, shownNow);
}
