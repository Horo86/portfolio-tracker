// Client checks that mirror the API's rules (D-89, auth/passwords.py), so most mistakes are
// explained next to the field before anything is sent. The API still checks everything.

export const USERNAME_PATTERN = /^[A-Za-z0-9._-]{3,32}$/;
export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 1024;

/** The translation key of the problem with a new password, or null. */
export function passwordProblem(password: string): string | null {
  if (password.length < PASSWORD_MIN) return "validation.passwordMin";
  if (password.length > PASSWORD_MAX) return "validation.passwordMax";
  return null;
}
