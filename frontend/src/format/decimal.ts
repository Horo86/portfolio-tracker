// Display formatting of decimal strings from the API (ADR-0004: money and quantities are
// strings). The digits never pass through a binary float: rounding and grouping work on the
// string. Intl gives only the symbols and their order for the language (separators, sign,
// currency symbol and its position), from formatting the constant 1.
// Display rounding is half away from zero (D-137); the engine's half-even rounding (§0) is
// unaffected because rounded values are never sent back.

const DECIMAL = /^(-)?(\d+)(?:\.(\d+))?$/;

type Parsed = { negative: boolean; integer: string; fraction: string };

function parse(value: string): Parsed {
  const match = DECIMAL.exec(value.trim());
  if (!match) throw new Error(`not a decimal string: ${value}`);
  return { negative: match[1] === "-", integer: match[2], fraction: match[3] ?? "" };
}

/** Rounds the digits to `places` decimals, half away from zero, on the absolute value. */
function round({ negative, integer, fraction }: Parsed, places: number): Parsed {
  if (fraction.length <= places) return { negative, integer, fraction };
  const roundUp = fraction.charCodeAt(places) >= "5".charCodeAt(0);
  let digits = integer + fraction.slice(0, places);
  if (roundUp) {
    const chars = digits.split("");
    let i = chars.length - 1;
    while (i >= 0 && chars[i] === "9") chars[i--] = "0";
    if (i >= 0) chars[i] = String(Number(chars[i]) + 1);
    else chars.unshift("1");
    digits = chars.join("");
  }
  const split = digits.length - places;
  return { negative, integer: digits.slice(0, split), fraction: digits.slice(split) };
}

function isZero({ integer, fraction }: Parsed): boolean {
  return /^0*$/.test(integer) && /^0*$/.test(fraction);
}

type Options = {
  minDecimals: number;
  maxDecimals: number;
  signed?: boolean; // "+" before a positive value (gains); zero has no sign
  currency?: string;
};

function separators(locale: string): { group: string; decimal: string } {
  const parts = new Intl.NumberFormat(locale).formatToParts(1234567.5);
  return {
    group: parts.find((p) => p.type === "group")?.value ?? ",",
    decimal: parts.find((p) => p.type === "decimal")?.value ?? ".",
  };
}

function group(integer: string, separator: string): string {
  const trimmed = integer.replace(/^0+(?=\d)/, "");
  return trimmed.replace(/\B(?=(\d{3})+(?!\d))/g, separator);
}

export function formatDecimal(value: string, locale: string, options: Options): string {
  const rounded = round(parse(value), options.maxDecimals);
  let fraction = rounded.fraction.replace(/0+$/, "");
  if (fraction.length < options.minDecimals) fraction = fraction.padEnd(options.minDecimals, "0");
  const zero = isZero(rounded);
  const sign = zero ? 0 : rounded.negative ? -1 : 1;

  // The template: Intl formats the constant ±1 (or 0) with the same symbols and sign rules.
  const template = new Intl.NumberFormat(locale, {
    ...(options.currency
      ? { style: "currency", currency: options.currency, currencyDisplay: "narrowSymbol" }
      : {}),
    signDisplay: options.signed ? "exceptZero" : "auto",
  }).formatToParts(sign);

  const { group: groupSep, decimal } = separators(locale);
  const number = group(rounded.integer, groupSep) + (fraction ? decimal + fraction : "");
  let placed = false;
  return template
    .map((part) => {
      if (part.type === "integer") {
        placed = true;
        return number;
      }
      if (part.type === "decimal" || part.type === "fraction") return "";
      return part.value;
    })
    .join("")
    .concat(placed ? "" : number);
}

/** Money: always 2 decimals (domain-rules §0). */
export function formatMoney(
  value: string,
  currency: string,
  locale: string,
  options: { signed?: boolean } = {},
): string {
  return formatDecimal(value, locale, { minDecimals: 2, maxDecimals: 2, currency, ...options });
}

/** Quantities: up to 8 decimals (D-68), trailing zeros dropped. */
export function formatQuantity(value: string, locale: string): string {
  return formatDecimal(value, locale, { minDecimals: 0, maxDecimals: 8 });
}
