// A readable name for a session's user agent ("Firefox on Linux"). Only the common browsers
// and systems are named; anything else is shown as sent.

const BROWSERS: [RegExp, string][] = [
  [/Edg(?:e|A|iOS)?\//, "Edge"],
  [/OPR\/|Opera/, "Opera"],
  [/Firefox\/|FxiOS\//, "Firefox"],
  [/Chrome\/|CriOS\//, "Chrome"],
  [/Safari\//, "Safari"],
];

const SYSTEMS: [RegExp, string][] = [
  [/Windows/, "Windows"],
  [/Android/, "Android"],
  [/iPhone|iPad|iPod/, "iOS"],
  [/Mac OS X|Macintosh/, "macOS"],
  [/CrOS/, "ChromeOS"],
  [/Linux/, "Linux"],
];

const find = (table: [RegExp, string][], text: string) =>
  table.find(([pattern]) => pattern.test(text))?.[1] ?? null;

/** Browser and system, or null when either is not recognized. */
export function deviceOf(userAgent: string): { browser: string; os: string } | null {
  const browser = find(BROWSERS, userAgent);
  const os = find(SYSTEMS, userAgent);
  return browser && os ? { browser, os } : null;
}
