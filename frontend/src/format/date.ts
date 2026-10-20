// Dates follow the interface language (docs/ui/README.md); a separate format setting is V2.

/** A calendar day from the API (YYYY-MM-DD): the same day in every time zone. */
export function formatDay(day: string, locale: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, date)),
  );
}

/** A moment from the API (ISO 8601 with its offset), in the browser's time zone. */
export function formatMoment(moment: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(moment),
  );
}
