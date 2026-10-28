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
  market_data: "ON" | "OFF"; // PT_MARKET_DATA: whether automatic prices are fetched (D-145)
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

/** Totals shared by an account (its currency) and the portfolio (the reporting currency):
 * §5, §6, §14. `valued_cost` is the base of the unrealized %: the invested capital unless
 * partial. */
export type Figures = {
  currency: string;
  day: string; // valuation day
  total_value: DecimalStr;
  securities_value: DecimalStr;
  cash: DecimalStr;
  invested_capital: DecimalStr;
  valued_cost: DecimalStr;
  unrealized: DecimalStr;
  unrealized_pct: DecimalStr | null;
  realized_gross: DecimalStr;
  realized_taxes: DecimalStr;
  realized_net: DecimalStr;
  realized_pct_gross: DecimalStr | null;
  realized_pct_net: DecimalStr | null;
  income_gross: DecimalStr;
  income_taxes: DecimalStr;
  income_net: DecimalStr;
  taxes: DecimalStr;
  gain_gross: DecimalStr;
  gain_net: DecimalStr;
  partial: boolean;
};

export type AccountSummary = Figures & {
  account_id: string;
  open_positions: number;
  stale_fx: StaleFx[];
  reporting: { currency: string; total_value: DecimalStr | null; stale_fx: StaleFx | null };
};

/** `GET /portfolio/summary`: every account in the reporting currency; an account without a
 * rate is left out and named by its currency (D-73). */
export type PortfolioSummary = Figures & {
  account_count: number;
  unavailable_currencies: string[];
  stale_fx: StaleFx[];
};

export type Share = { key: string; value: DecimalStr; share: DecimalStr }; // share: a ratio
export type AssetShare = { asset_id: string; name: string; value: DecimalStr; share: DecimalStr };
export type IncomeAmounts = { gross: DecimalStr; taxes: DecimalStr; net: DecimalStr };

/** `GET /portfolio/breakdown` or `GET /accounts/{id}/breakdown` (D-153): allocation and the
 * dividends and coupons received, in the view's currency, largest parts first. */
export type Breakdown = {
  currency: string;
  day: string;
  partial: boolean;
  unavailable_currencies: string[];
  by_type: Share[]; // key: an asset type or CASH
  by_currency: Share[];
  by_asset: AssetShare[];
  negative_cash: DecimalStr; // left out of the shares
  income_by_month: (IncomeAmounts & { month: string })[]; // YYYY-MM, the last 12 months
  income_by_year: (IncomeAmounts & { year: number })[];
  income_total: IncomeAmounts;
};

export type PriceSource = "PROVIDER" | "MANUAL";

/** One holding (`GET /accounts/{id}/positions` row, or a `/portfolio/positions` row): amounts
 * in the account currency, or in the reporting currency over all accounts (D-143). Ratios
 * ("0.1" = +10 %) are null when their base is zero. */
export type Holding = {
  asset_id: string;
  name: string;
  ticker: string | null;
  isin: string | null;
  asset_type: AssetType;
  quote_currency: string;
  quote_unit: QuoteUnit;
  open: boolean;
  quantity: DecimalStr; // bonds: nominal
  pmc: DecimalStr | null; // average cost; bonds as % of nominal
  price: DecimalStr | null; // close used, in the quote unit
  price_date: string | null;
  price_stale: boolean;
  price_source: PriceSource | null;
  price_exchange: string | null; // PROVIDER: the exchange the close comes from (D-152)
  price_exchange_code: string | null; // PROVIDER: the engine's code of that exchange
  // OK | MANUAL | PENDING | NOT_AVAILABLE | MISMATCH | FAILING (strings: a newer server may add)
  price_status: string;
  price_status_detail: string | null; // MISMATCH: CURRENCY | UNIT | LEVEL; FAILING: NOT_LISTED
  estimated_at_cost: boolean;
  value_unavailable: boolean;
  value: DecimalStr | null;
  cost_basis: DecimalStr;
  unrealized: DecimalStr | null;
  unrealized_pct: DecimalStr | null;
  realized_gross: DecimalStr;
  realized_taxes: DecimalStr;
  realized_net: DecimalStr;
  realized_pct_gross: DecimalStr | null;
  realized_pct_net: DecimalStr | null;
  income_gross: DecimalStr;
  income_taxes: DecimalStr;
  income_net: DecimalStr;
  taxes: DecimalStr;
  gain_gross: DecimalStr | null;
  gain_net: DecimalStr | null;
};

export type AccountPosition = Holding & { fx_stale: StaleFx | null };

export type PortfolioHolding = Holding & {
  account_ids: string[];
  valued_cost: DecimalStr;
  partial: boolean; // a part could not be valued
  stale_fx: StaleFx[];
};

export type PortfolioPositions = {
  currency: string;
  day: string;
  partial: boolean;
  unavailable_currencies: string[];
  stale_fx: StaleFx[];
  positions: PortfolioHolding[];
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

/** One of the user's own manual prices of a security (D-116, D-149), in its quote unit. */
export type ManualPrice = { id: string; day: string; close: DecimalStr; source: "MANUAL" };

/** One provider result of `POST /assets/search` (D-128): nothing stored. Adding sends its
 * ISIN and currency; the listing that prices it is chosen later, never here (D-125). */
export type SearchResult = {
  name: string | null;
  symbol: string; // the provider's, for display
  exchange: string | null;
  currency: string;
  quote_unit: QuoteUnit;
  asset_type: AssetType | null; // the provider's proposal
  isin: string | null; // null for a text search
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

/** Creates saved together (D-147): "add holdings I already own", 1 to 50 rows. */
export type BatchIn = { transactions: TransactionFields[] };

/** A warning of a batch dry run: `index` is the row of the request it is about, if new. */
export type BatchWarning = LedgerWarning & { index: number | null };

/** The dry run of a batch (D-147): each row completed, nothing stored, no cash summary. */
export type BatchPreview = {
  transactions: TransactionFields[]; // what Save sends to POST /transactions/batch
  derived: ("gross_amount" | "total_amount")[][]; // per row
  warnings: BatchWarning[];
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

// --- Imports (api/routes/imports.py; D-100, D-113, D-142) ----------------------------------

export type ImportFormat = "directa-xlsx" | "app-csv";

export type ImportCategory =
  | "TO_IMPORT"
  | "TO_REVIEW"
  | "SKIPPED_BY_CHOICE"
  | "AMBIGUOUS"
  | "NOT_RECOGNIZED"
  | "INVALID"
  | "DUPLICATE";

/** A stable code with its parameters (strings), translated by the screen (importText.ts). */
export type ImportReason = { code: string; params: Record<string, string> };

export type ImportAssetChoice = {
  asset_id?: string | null; // an existing asset (only for a CSV row without ISIN)
  asset_type?: AssetType | null;
  quote_currency?: string | null;
  quote_unit?: QuoteUnit | null;
};

/** The file and the user's choices: the confirmation sends the same values again. */
export type ImportIn = {
  content_base64: string;
  file_name?: string | null;
  format?: ImportFormat | null; // null: detected by the server
  move_cash: boolean; // false: "do not move cash"
  account_id?: string | null; // Directa
  accounts?: Record<string, string | null>; // app CSV: file account name -> account id or new
  assets?: Record<string, ImportAssetChoice>; // by asset key
  selected?: string[] | null; // line keys; null: the default selection
};

export type ImportConfirmIn = ImportIn & { fingerprint: string; selected: string[] };

export type ImportLine = {
  key: string;
  category: ImportCategory;
  selectable: boolean;
  selected: boolean;
  lines: number[]; // spreadsheet rows of the file
  reasons: ImportReason[];
  info: ImportReason[];
  account: string | null; // key of `accounts`
  asset: string | null; // key of `assets`
  type: TransactionType | null;
  trade_date: string | null;
  trade_time: string | null;
  value_date: string | null;
  quantity: DecimalStr | null;
  unit_price: DecimalStr | null;
  gross_amount: DecimalStr | null;
  fees: DecimalStr | null;
  accrued_interest_paid: DecimalStr | null;
  accrued_interest_received: DecimalStr | null;
  taxes: DecimalStr | null;
  total_amount: DecimalStr | null;
  split_ratio: string | null;
  affects_cash: boolean;
  external_ref: string | null;
  notes: string | null;
  metadata: Record<string, unknown> | null;
  account_expense: boolean;
  original_isin: string | null;
  original_name: string | null;
};

export type ImportAccountPlan = {
  key: string | null; // the CSV account name; null for a Directa file
  account_id: string | null; // null: created at confirmation
  new: boolean;
  name: string;
  currency: string | null;
  provider: string | null;
};

export type ImportAssetPlan = {
  key: string;
  asset_id: string | null; // null: a new private asset
  new: boolean;
  name: string;
  isin: string | null;
  ticker: string | null;
  asset_type: AssetType | null; // null: the user must choose
  quote_currency: string | null;
  quote_unit: QuoteUnit | null;
  options: string[]; // the user's assets with the same ticker (CSV row without ISIN)
};

export type ImportWarning = {
  code: WarningCode;
  date: string;
  account: string | null; // key of `accounts`
  line: string | null; // preview line key
  transaction_id: string | null; // an existing transaction
  introduced: boolean;
};

export type ImportCounts = {
  selected: number;
  to_import: number;
  to_review: number;
  skipped_by_choice: number;
  duplicates: number;
  not_recognized: number;
  ambiguous: number;
  invalid: number;
};

export type ImportPreview = {
  format: ImportFormat;
  file_name: string | null;
  rows_read: number;
  move_cash: boolean;
  account_code: string | null;
  counts: ImportCounts;
  accounts: ImportAccountPlan[];
  assets: ImportAssetPlan[];
  lines: ImportLine[];
  notices: ImportReason[];
  blocking: ImportReason[];
  warnings: ImportWarning[];
  fingerprint: string;
};

export type ImportBatch = {
  id: string;
  account_id: string;
  format: ImportFormat;
  file_name: string | null;
  move_cash: boolean;
  imported: number;
  duplicates: number;
  not_recognized: number;
  to_review_skipped: number;
  ambiguous: number;
  invalid: number;
  skipped_by_choice: number;
  transactions: number;
  created_at: string;
};

export type ImportConfirmOut = {
  batches: ImportBatch[];
  transactions: Transaction[];
  accounts_created: string[];
  assets_created: string[];
  warnings: ImportWarning[];
};
