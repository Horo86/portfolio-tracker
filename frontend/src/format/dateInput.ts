// A day typed as text (M5 slice 6): both languages write the day first (gg/mm/aaaa,
// dd/mm/yyyy), whatever the browser's own date fields would do.

export type DayInputProblem = "format" | "impossible";

export type DayInput =
  | { kind: "empty" }
  | { kind: "ok"; value: string } // YYYY-MM-DD, as the API takes it
  | { kind: "error"; problem: DayInputProblem };

const TYPED = /^(\d{1,2})([/.-])(\d{1,2})\2([1-9]\d{3})$/;
const API_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

function daysIn(month: number, year: number): number {
  if (month === 2) return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/** Reads "28/10/2026", "5-3-2026" or "5.3.2026" (the same separator twice, a four-digit
 * year) into "2026-10-28"; a day that does not exist (31/02) is refused, never moved on. */
export function parseDayInput(text: string): DayInput {
  const typed = text.trim();
  if (typed === "") return { kind: "empty" };
  const parts = TYPED.exec(typed);
  if (!parts) return { kind: "error", problem: "format" };
  const [day, month, year] = [Number(parts[1]), Number(parts[3]), Number(parts[4])];
  if (month < 1 || month > 12 || day < 1 || day > daysIn(month, year)) {
    return { kind: "error", problem: "impossible" };
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  return { kind: "ok", value: `${year}-${pad(month)}-${pad(day)}` };
}

const NOT_A_DAY = "typed:";

/** What a date field hands out for a text that is not a day: marked, so that a text typed
 * in the API's order ("2026-10-28", refused) never passes for that day. */
export function notADay(text: string): string {
  return `${NOT_A_DAY}${text}`;
}

/** Whether a value is an API day (YYYY-MM-DD), not a text still being typed. */
export function isApiDay(value: string): boolean {
  return API_DAY.test(value);
}

/** An API day as the field shows it, "28/10/2026"; a text that is not a day as it was typed;
 * nothing for nothing. */
export function dayToInput(value: string): string {
  if (value.startsWith(NOT_A_DAY)) return value.slice(NOT_A_DAY.length);
  const parts = API_DAY.exec(value);
  return parts ? `${parts[3]}/${parts[2]}/${parts[1]}` : value;
}
