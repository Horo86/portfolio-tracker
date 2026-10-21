// Client checks that mirror the API's rules (D-89, auth/passwords.py), so most mistakes are
// explained next to the field before anything is sent. The API still checks everything.

export const USERNAME_PATTERN = /^[A-Za-z0-9._-]{3,32}$/;
export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 1024;

/** The translation key of the problem with a new password, or null. The length is counted
 * in characters (code points), like the server: an emoji is one, not two UTF-16 units. */
export function passwordProblem(password: string): string | null {
  const length = [...password].length;
  if (length < PASSWORD_MIN) return "validation.passwordMin";
  if (length > PASSWORD_MAX) return "validation.passwordMax";
  return null;
}

const ISIN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

/** The ISIN as the API reads it: spaces around it dropped, letters in capitals. */
export function normalIsin(text: string): string {
  return text.trim().toUpperCase();
}

/** The translation key of the problem with an ISIN (ISO 6166, `textformats.py`), or null.
 * The check digit is the Luhn digit of the code with each letter written as its number
 * (A = 10 … Z = 35). */
export function isinProblem(isin: string): string | null {
  if (!ISIN.test(isin)) return "validation.isinFormat";
  const digits = [...isin.slice(0, -1)].map((ch) => String(Number.parseInt(ch, 36))).join("");
  let total = 0;
  [...digits].reverse().forEach((ch, i) => {
    const n = Number(ch) * (i % 2 === 0 ? 2 : 1);
    total += Math.floor(n / 10) + (n % 10);
  });
  return (10 - (total % 10)) % 10 === Number(isin.at(-1)) ? null : "validation.isinCheck";
}
