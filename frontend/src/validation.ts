// Client checks that mirror the API's rules (D-89, D-173, identity.py, auth/passwords.py), so
// most mistakes are explained next to the field before anything is sent. The API still checks
// everything. Lengths are counted in code points after NFC, like the server.

const USERNAME_CHAR = /^(?:\p{L}|\p{M}|[0-9._@+-])$/u;
const NAME_CHAR = /^(?:\p{L}|\p{M}|[ '\u2019-])$/u;
const LETTER = /^\p{L}$/u;
const EMAIL_LOCAL = /^[a-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}$/;
const EMAIL_LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
export const NAME_MAX = 100;

/** The username as the API stores it: NFC, without surrounding spaces. */
export function normalUsername(text: string): string {
  return text.normalize("NFC").trim();
}

/** The translation key of the problem with a username (`normalUsername`'s result), or null:
 * 3 to 64 letters of any alphabet, digits or . _ - @ +, no spaces (D-173). */
export function usernameProblem(username: string): string | null {
  const chars = [...username];
  const fits = chars.length >= 3 && chars.length <= 64 && !/^\p{M}/u.test(username);
  return fits && chars.every((ch) => USERNAME_CHAR.test(ch)) ? null : "validation.usernamePattern";
}

/** A first or last name as the API stores it: NFC, trimmed, inner spaces collapsed. */
export function normalName(text: string): string {
  return text.normalize("NFC").replace(/\s+/gu, " ").trim();
}

/** The translation key of the problem with a name (`normalName`'s result), or null: 1 to 100
 * letters, spaces, apostrophes or hyphens, starting with a letter (D-173). */
export function nameProblem(name: string): string | null {
  if (name === "") return "validation.required";
  const chars = [...name];
  const fits = chars.length <= NAME_MAX && LETTER.test(chars[0]);
  return fits && chars.every((ch) => NAME_CHAR.test(ch)) ? null : "validation.namePattern";
}

/** The e-mail as the API stores it: trimmed and lowercased. */
export function normalEmail(text: string): string {
  return text.trim().toLowerCase();
}

/** The translation key of the problem with an e-mail (`normalEmail`'s result), or null. */
export function emailProblem(email: string): string | null {
  if (email === "") return "validation.required";
  const parts = email.split("@");
  if (email.length > 254 || parts.length !== 2) return "validation.emailPattern";
  const [local, domain] = parts;
  const localFits = EMAIL_LOCAL.test(local) && !/^\.|\.$|\.\./.test(local);
  const labels = domain.split(".");
  const domainFits = labels.length >= 2 && labels.every((label) => EMAIL_LABEL.test(label));
  return localFits && domainFits ? null : "validation.emailPattern";
}

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
