import type {
  SecuritySettingName,
  SecuritySettings,
  SecuritySettingValues,
} from "./api/types";

// The security settings' form (D-182), pure: what is typed, its checks and what changes.
// The bounds come from the server's answer; the two cross rules are the server's (D-177).

export const SECURITY_FIELDS: readonly SecuritySettingName[] = [
  "session_idle_days",
  "session_absolute_days",
  "login_account_failures",
  "login_address_failures",
  "block_first_minutes",
  "block_max_minutes",
];

export type Drafts = Record<SecuritySettingName, string>;

/** A problem of one field: a translation key and its values. */
export type Problem = { key: string; values?: Record<string, number> };

export type Problems = Partial<Record<SecuritySettingName, Problem>>;

/** The whole number typed (digits only, spaces around allowed), or null. */
export function parseWhole(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d{1,6}$/.test(trimmed)) return null;
  return Number(trimmed);
}

export function draftsOf(values: SecuritySettingValues): Drafts {
  return Object.fromEntries(
    SECURITY_FIELDS.map((name) => [name, String(values[name])]),
  ) as Drafts;
}

/** Every field's problem: not a whole number, out of its range, then the two cross rules for
 * fields that are themselves valid (idle at most absolute; per address at least twice per
 * user). Empty when the values can be sent. */
export function problemsOf(drafts: Drafts, bounds: SecuritySettings["bounds"]): Problems {
  const found: Problems = {};
  const good: Partial<SecuritySettingValues> = {};
  for (const name of SECURITY_FIELDS) {
    const value = parseWhole(drafts[name]);
    const { min, max } = bounds[name];
    if (value === null) found[name] = { key: "securitySettings.errors.whole" };
    else if (value < min || value > max)
      found[name] = { key: "securitySettings.errors.range", values: { min, max } };
    else good[name] = value;
  }
  const idle = good.session_idle_days;
  const absolute = good.session_absolute_days;
  if (idle !== undefined && absolute !== undefined && idle > absolute) {
    found.session_idle_days = {
      key: "securitySettings.errors.idleOverAbsolute",
      values: { max: absolute },
    };
  }
  const account = good.login_account_failures;
  const address = good.login_address_failures;
  if (account !== undefined && address !== undefined && address < 2 * account) {
    found.login_address_failures = {
      key: "securitySettings.errors.addressUnderAccount",
      values: { min: 2 * account },
    };
  }
  return found;
}

/** The values to send; call only when `problemsOf` found nothing. */
export function valuesOf(drafts: Drafts): SecuritySettingValues {
  return Object.fromEntries(
    SECURITY_FIELDS.map((name) => [name, parseWhole(drafts[name])]),
  ) as SecuritySettingValues;
}

export type Change = { name: SecuritySettingName; from: number; to: number };

/** What differs from the values in force, in the form's order. A field that is not a whole
 * number counts as changed when its text differs from the value in force. */
export function changesOf(drafts: Drafts, current: SecuritySettingValues): Change[] {
  return SECURITY_FIELDS.flatMap((name) => {
    const to = parseWhole(drafts[name]);
    if (to === null || to === current[name]) return [];
    return [{ name, from: current[name], to }];
  });
}

/** Whether "Salva" can be pressed: something typed differs from the values in force. */
export function isChanged(drafts: Drafts, current: SecuritySettingValues): boolean {
  return SECURITY_FIELDS.some((name) => drafts[name].trim() !== String(current[name]));
}
