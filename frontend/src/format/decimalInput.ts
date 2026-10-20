// Typed numbers become decimal strings (D-140), without Number or any binary float: the
// digits are only checked and moved. The language's decimal separator is always accepted.
// The other symbol ("." in Italian, "," in English) groups thousands when the text is a valid
// grouping ("1.234", "1.234.567": a first group of 1-3 digits not starting with 0, then
// groups of exactly 3); otherwise, appearing once, it is the decimal separator ("1.5",
// "12.34", "0.234"). The form shows the number as read, so a wrong reading is visible.

export const MAX_DECIMALS = 8; // quantities (D-68); amounts and prices follow the same limit
export const MAX_INTEGER_DIGITS = 15; // D-98

export type InputProblem = "invalid" | "negative" | "tooManyDecimals" | "tooLarge";

export type DecimalInput =
  | { kind: "empty" }
  | { kind: "ok"; value: string }
  | { kind: "error"; problem: InputProblem };

/** The decimal separator of the language, from Intl. */
export function decimalSeparator(locale: string): string {
  const parts = new Intl.NumberFormat(locale).formatToParts(1.5);
  return parts.find((p) => p.type === "decimal")?.value ?? ".";
}

const escape = (symbol: string) => symbol.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const count = (text: string, symbol: string) => text.split(symbol).length - 1;

/** Reads a typed number. `signed`: a minus sign is allowed (taxes); otherwise it is refused. */
export function parseDecimalInput(
  text: string,
  locale: string,
  { signed = false }: { signed?: boolean } = {},
): DecimalInput {
  // Spaces (also the no-break ones Intl writes) group digits; the typographic minus is "-".
  const compact = text.replace(/[\s\u00a0\u202f]/g, "").replace(/\u2212/g, "-");
  if (compact === "") return { kind: "empty" };

  const signMatch = /^[+-]/.exec(compact);
  const negative = signMatch?.[0] === "-";
  const body = signMatch ? compact.slice(1) : compact;
  if (!/^[\d.,]+$/.test(body)) return { kind: "error", problem: "invalid" };

  const decimal = decimalSeparator(locale);
  const other = decimal === "," ? "." : ",";
  const grouping = new RegExp(`^[1-9]\\d{0,2}(?:${escape(other)}\\d{3})+$`);

  let integer: string;
  let fraction: string;
  if (count(body, decimal) > 1) return { kind: "error", problem: "invalid" };
  if (count(body, decimal) === 1) {
    [integer, fraction] = body.split(decimal);
    if (fraction.includes(other)) return { kind: "error", problem: "invalid" };
    if (integer.includes(other)) {
      if (!grouping.test(integer)) return { kind: "error", problem: "invalid" };
      integer = integer.split(other).join("");
    }
  } else if (grouping.test(body)) {
    integer = body.split(other).join("");
    fraction = "";
  } else if (count(body, other) <= 1) {
    [integer, fraction = ""] = body.split(other);
  } else {
    return { kind: "error", problem: "invalid" };
  }
  if (integer === "" && fraction === "") return { kind: "error", problem: "invalid" };

  integer = integer.replace(/^0+(?=\d)/, "") || "0";
  if (fraction.length > MAX_DECIMALS) return { kind: "error", problem: "tooManyDecimals" };
  if (integer.length > MAX_INTEGER_DIGITS) return { kind: "error", problem: "tooLarge" };
  const zero = /^0+$/.test(integer + fraction || "0");
  if (negative && !zero && !signed) return { kind: "error", problem: "negative" };
  const sign = negative && !zero ? "-" : "";
  return { kind: "ok", value: `${sign}${integer}${fraction ? `.${fraction}` : ""}` };
}

/** A decimal string from the API as the text of an input in the language: its decimal
 * separator, no grouping, every digit kept ("1234.50" is "1234,50" in Italian). */
export function decimalToInput(value: string, locale: string): string {
  return value.replace(".", decimalSeparator(locale));
}
