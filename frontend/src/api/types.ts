// API types, written by hand (M5 planning, Q-12). Money and quantities are decimal strings
// (DecimalStr): never converted to numbers in the browser.

export type DecimalStr = string;
export type Language = "it" | "en";
export type ThemeName = "sobrio" | "moderno" | "ambra" | "grafite";
export type Role = "INVESTOR" | "ADMINISTRATOR";

export type Health = { status: "ok" | "error"; version: string; database: "ok" | "error" };

export type SetupStatus = { setup_required: boolean };

export type Me = {
  id: string;
  username: string;
  display_name: string | null;
  role: Role;
  language: Language;
  theme: ThemeName;
  reporting_currency: string;
  must_change_password: boolean;
  two_factor_enabled: boolean;
  password_changed_at: string;
  last_login_at: string | null;
};

export type LoginOut = { user: Me; token: string | null; expires_at: string };

export type SessionInfo = {
  id: string;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  user_agent: string | null;
  current: boolean;
};

export type Account = {
  id: string;
  name: string;
  provider: string | null;
  currency: string;
  external_code: string | null;
  created_at: string;
  transaction_count: number;
  currency_locked: boolean;
};

export type StaleFx = { from_currency: string; to_currency: string; rate_date: string };

export type AccountSummary = {
  account_id: string;
  currency: string;
  day: string;
  total_value: DecimalStr;
  securities_value: DecimalStr;
  cash: DecimalStr;
  invested_capital: DecimalStr;
  gain_gross: DecimalStr;
  gain_net: DecimalStr;
  partial: boolean;
  open_positions: number;
  stale_fx: StaleFx[];
  reporting: { currency: string; total_value: DecimalStr | null; stale_fx: StaleFx | null };
};
