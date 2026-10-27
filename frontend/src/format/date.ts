// Dates follow the interface language (docs/ui/README.md); a separate format setting is V2.

/** A calendar day from the API (YYYY-MM-DD): the same day in every time zone. */
export function formatDay(day: string, locale: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, date)),
  );
}

/** A month from the API (YYYY-MM), e.g. "ott 2026": the same month in every time zone. */
export function formatMonth(month: string, locale: string): string {
  const [year, number] = month.split("-").map(Number);
  return new Intl.DateTimeFormat(locale, { month: "short", year: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, number - 1, 1)),
  );
}

/** A moment from the API (ISO 8601 with its offset), in the browser's time zone. */
export function formatMoment(moment: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(moment),
  );
}

/** The day of a moment from the API, in the browser's time zone. */
export function formatMomentDay(moment: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(moment));
}

/** Today in the browser's time zone, as the date input writes it. */
export function today(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
