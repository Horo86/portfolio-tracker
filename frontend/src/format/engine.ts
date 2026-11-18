// The price engines by the name shown to the user; the keys are the backend's engine names
// (marketdata/*.py `NAME`, the same keys the administration's price sources card lists).
const ENGINE_NAMES: Readonly<Record<string, string>> = {
  yahoo: "Yahoo Finance",
  openfigi: "OpenFIGI",
  frankfurter: "Frankfurter",
};

/** The name shown for an engine; an unknown key is shown as it is. */
export function engineName(key: string): string {
  return Object.hasOwn(ENGINE_NAMES, key) ? ENGINE_NAMES[key] : key;
}
