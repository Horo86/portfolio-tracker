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

export type TransactionType =
  | "BUY"
  | "SELL"
  | "DIVIDEND"
  | "COUPON"
  | "DEPOSIT"
  | "WITHDRAWAL"
  | "STOCK_SPLIT";

export type AssetType = "ETF" | "STOCK" | "BOND";
export type QuoteUnit = "CURRENCY" | "MINOR_CURRENCY_UNIT" | "PERCENT_OF_NOMINAL";

export type Asset = {
  id: string;
  isin: string | null;
  ticker: string | null;
  name: string;
  asset_type: AssetType;
  quote_currency: string;
  quote_unit: QuoteUnit;
  shared: boolean;
  in_use: boolean;
  created_at: string;
};

/** The writable fields of a transaction (POST and PUT bodies). A field the type does not
 * have is left out (D-62). */
export type TransactionFields = {
  account_id: string;
  type: TransactionType;
  trade_date: string;
  trade_time?: string | null;
  value_date?: string | null;
  asset_id?: string | null;
  quantity?: DecimalStr | null;
  unit_price?: DecimalStr | null;
  gross_amount?: DecimalStr | null;
  fees?: DecimalStr | null;
  accrued_interest_paid?: DecimalStr | null;
  accrued_interest_received?: DecimalStr | null;
  taxes?: DecimalStr | null;
  total_amount?: DecimalStr | null;
  split_ratio?: string | null;
  affects_cash?: boolean;
  external_ref?: string | null;
  notes?: string | null;
  metadata?: Record<string, unknown> | null;
};

export type Transaction = Required<TransactionFields> & {
  id: string;
  sequence: number;
  created_at: string;
  updated_at: string;
};

export type TransactionPage = { items: Transaction[]; total: number };

export type WarningCode = "NEGATIVE_CASH" | "INCOME_WITHOUT_HOLDING" | "FUTURE_DATE";

export type LedgerWarning = {
  account_id: string;
  code: WarningCode;
  date: string;
  transaction_id: string | null;
  introduced: boolean;
};

export type ChangeOut = {
  transactions: Transaction[];
  deleted: string[];
  warnings: LedgerWarning[];
};

/** A dry run of one change (D-139): a create, an edit (`replaces` + `transaction`) or a
 * delete (`replaces` alone). */
export type PreviewIn = { replaces?: string; transaction?: TransactionFields };

export type Preview = {
  transaction: TransactionFields | null; // what Save sends; null for a delete
  derived: ("gross_amount" | "total_amount")[];
  account_id: string;
  currency: string;
  cash: { before: DecimalStr; change: DecimalStr; after: DecimalStr };
  position: {
    asset_id: string;
    quantity_before: DecimalStr;
    quantity_after: DecimalStr;
    cost_basis_before: DecimalStr;
    cost_basis_after: DecimalStr;
    pmc_before: DecimalStr | null; // bonds as % of nominal (D-66)
    pmc_after: DecimalStr | null;
  } | null;
  warnings: LedgerWarning[];
};

/** `extra.conflict` of a 409 QUANTITY_CONFLICT. */
export type QuantityConflict = {
  reason: "NEGATIVE_QUANTITY" | "SPLIT_TO_ZERO";
  transaction_id?: string;
  index?: number; // a transaction of the request that has no id yet
  account_id: string;
  asset_id: string;
  quantity: DecimalStr; // what it would become
  held: DecimalStr; // what was held just before
};

export type FieldIssue = { field: string; code: string };

/** One entry of `extra.transactions` of a 422 VALIDATION_FAILED. */
export type TransactionIssues = {
  transaction_id?: string;
  index?: number;
  issues: FieldIssue[];
};
