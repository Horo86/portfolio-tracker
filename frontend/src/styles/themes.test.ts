import { afterEach, describe, expect, it } from "vitest";

import { applyTheme, DEFAULT_THEME, storedTheme, THEMES } from "../theme";
import css from "./themes.css?raw";

// --- tokens read from the real stylesheet ----------------------------------------------

function blocks(): Map<string, Record<string, string>> {
  const result = new Map<string, Record<string, string>>();
  for (const [, selector, body] of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const tokens: Record<string, string> = {};
    for (const [, name, value] of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) tokens[name] = value.trim();
    result.set(selector.replace(/\/\*[\s\S]*?\*\//g, "").trim(), tokens);
  }
  return result;
}

function tokensOf(theme: string): Record<string, string> {
  const all = blocks();
  const shared = [...all.entries()].find(([selector]) => selector === ":root")?.[1] ?? {};
  const own = [...all.entries()].find(([selector]) => selector.includes(`"${theme}"`))?.[1];
  if (!own) throw new Error(`no block for ${theme}`);
  const tokens = { ...shared, ...own };
  const resolve = (value: string): string => {
    const ref = /^var\(--([\w-]+)\)$/.exec(value);
    return ref ? resolve(tokens[ref[1]]) : value;
  };
  return Object.fromEntries(Object.entries(tokens).map(([k, v]) => [k, resolve(v)]));
}

// --- WCAG 2.1 contrast -------------------------------------------------------------------

function luminance(hex: string): number {
  const [r, g, b] = (hex.replace("#", "").match(/../g) ?? []).map((h) => {
    const v = Number.parseInt(h, 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// [foreground, background, minimum]: 4.5 for text (1.4.3), 3 for control outlines (1.4.11).
const PAIRS: [string, string, number][] = [
  ["ink", "bg", 4.5],
  ["ink", "surface", 4.5],
  ["ink", "soft", 4.5],
  ["muted", "bg", 4.5],
  ["muted", "surface", 4.5],
  ["muted", "soft", 4.5],
  ["accent", "bg", 4.5],
  ["accent", "surface", 4.5],
  ["surface", "accent", 4.5],
  ["surface", "negative", 4.5],
  ["nav-ink", "nav", 4.5],
  ["nav-muted", "nav", 4.5],
  ["nav-ink", "nav-active", 4.5],
  ["positive", "surface", 4.5],
  ["positive", "bg", 4.5],
  ["negative", "surface", 4.5],
  ["negative", "bg", 4.5],
  ["positive-strong", "positive-tint", 4.5],
  ["negative-strong", "negative-tint", 4.5],
  ["warning-ink", "warning-bg", 4.5],
  ["field-border", "surface", 3],
  ["field-border", "bg", 3],
];

describe("color themes", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("data-theme");
    window.localStorage.clear();
  });

  it.each(THEMES)("%s meets WCAG AA contrast on every text and control pair", (theme) => {
    const tokens = tokensOf(theme);
    for (const [fg, bg, minimum] of PAIRS) {
      expect(tokens[fg], `${theme}: --${fg}`).toMatch(/^#[0-9a-f]{6}$/i);
      expect(tokens[bg], `${theme}: --${bg}`).toMatch(/^#[0-9a-f]{6}$/i);
      const ratio = contrast(tokens[fg], tokens[bg]);
      expect(ratio, `${theme}: --${fg} on --${bg} = ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(
        minimum,
      );
    }
  });

  it("applies a theme at once and remembers it in this browser", () => {
    applyTheme("ambra");
    expect(document.documentElement.dataset.theme).toBe("ambra");
    expect(storedTheme()).toBe("ambra");
  });

  it("falls back to Moderno for a missing or old value (M0 had system/light/dark)", () => {
    expect(storedTheme()).toBe(DEFAULT_THEME);
    window.localStorage.setItem("pt.theme", "dark");
    expect(storedTheme()).toBe("moderno");
  });
});
