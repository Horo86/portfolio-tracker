/** A file size in the app's language: bytes, kB, MB or GB (powers of 1024, as disks show). */
export function formatSize(bytes: number, locale: string): string {
  const units = ["B", "kB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : 1;
  const number = new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value);
  return `${number} ${units[unit]}`;
}
