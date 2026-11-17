import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyAppearance,
  applyTheme,
  DEFAULT_THEME,
  shownTheme,
  storedAppearance,
  storedTheme,
  THEMES,
  watchSystem,
} from "../theme";
import styles from "../styles.css?raw";
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
  // The dashboard's chart colors on its cards (1.4.11, graphical objects).
  ["chart-1", "surface", 3],
  ["chart-2", "surface", 3],
  ["chart-3", "surface", 3],
  ["chart-4", "surface", 3],
];

describe("color themes", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("data-theme");
    window.localStorage.clear();
  });

  // The four colour themes and dark (D-176).
  it.each([...THEMES, "scuro"])("%s meets WCAG AA contrast on every text and control pair", (theme) => {
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

  it("dark: the accent stays apart from the soft background of a selected item", () => {
    const tokens = tokensOf("scuro");
    expect(contrast(tokens.accent, tokens.soft)).toBeGreaterThanOrEqual(3);
  });

  it("the colour before the script runs on a dark device is dark's background", () => {
    const rule = /@media \(prefers-color-scheme: dark\)\s*\{\s*html:not\(\[data-theme\]\)\s*\{([^}]*)\}/.exec(
      styles,
    );
    expect(rule, "the no-flash rule in styles.css").not.toBeNull();
    expect(/--bg:\s*(#[0-9a-f]{6});/i.exec(rule![1])?.[1]).toBe(tokensOf("scuro").bg);
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

// --- light or dark (D-176) -------------------------------------------------------------

/** A device that prefers dark or light, with listeners to fire a live change. */
function stubDevice(dark: boolean) {
  const listeners = new Set<() => void>();
  const query = {
    get matches() {
      return dark;
    },
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  };
  vi.stubGlobal("matchMedia", (text: string) => {
    expect(text).toBe("(prefers-color-scheme: dark)");
    return query;
  });
  return {
    switchTo(next: boolean) {
      dark = next;
      for (const listener of listeners) listener();
    },
    listeners,
  };
}

describe("light or dark", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("data-theme");
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("follows a dark device by default, keeping the colour theme remembered", () => {
    stubDevice(true);
    expect(storedAppearance()).toBe("system");
    applyTheme("ambra");
    expect(document.documentElement.dataset.theme).toBe("scuro");
    expect(window.localStorage.getItem("pt.theme")).toBe("ambra");
  });

  it("stays light without matchMedia (jsdom, old browsers)", () => {
    applyTheme("sobrio");
    expect(document.documentElement.dataset.theme).toBe("sobrio");
  });

  it("light chosen in this browser wins over a dark device", () => {
    stubDevice(true);
    applyTheme("grafite");
    applyAppearance("light");
    expect(document.documentElement.dataset.theme).toBe("grafite");
    expect(window.localStorage.getItem("pt.appearance")).toBe("light");
  });

  it("dark chosen in this browser wins over a light device", () => {
    stubDevice(false);
    applyTheme("moderno");
    expect(document.documentElement.dataset.theme).toBe("moderno");
    applyAppearance("dark");
    expect(document.documentElement.dataset.theme).toBe("scuro");
    expect(shownTheme("ambra")).toBe("scuro");
  });

  it("follows a live change of the device, and stops when asked", () => {
    const device = stubDevice(false);
    applyTheme("ambra");
    const stop = watchSystem();
    device.switchTo(true);
    expect(document.documentElement.dataset.theme).toBe("scuro");
    device.switchTo(false);
    expect(document.documentElement.dataset.theme).toBe("ambra");
    stop();
    expect(device.listeners.size).toBe(0);
  });

  it("an unknown stored value means the same as the device", () => {
    stubDevice(true);
    window.localStorage.setItem("pt.appearance", "sepia");
    expect(storedAppearance()).toBe("system");
    applyTheme("moderno");
    expect(document.documentElement.dataset.theme).toBe("scuro");
  });
});
