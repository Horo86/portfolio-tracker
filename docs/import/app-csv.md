# App CSV format (import and export) — version 1

Status: final for V1 (M3; D-38, D-103, D-107, D-110, D-111, D-112). One documented format used both for the per-user **CSV export** and for **CSV import**, so that data can leave and re-enter the application (data ownership) and so that users of brokers without a dedicated importer have a path. Exporting and re-importing must reproduce the data without duplicates (D-38).

## Format

- UTF-8 (a leading byte-order mark is accepted on import and not written on export), comma separator, `"` quoting, header row, one transaction per row. Row numbers in messages are spreadsheet rows: the header is row 1 (a quoted cell with a line break stays in one row).
- Decimal separator `.`; no thousands separator; dates `YYYY-MM-DD`; times `HH:MM` (optional; `HH:MM:SS` and fractions of a second are accepted and written back as stored); booleans `true`/`false`.
- **An empty cell means "not given"**, which is not the same as `0`: a field that a type does not have must be empty (D-62, D-67).
- Rows may be in any order (the ledger is replayed deterministically, `domain-rules.md` §4).
- **Version marker (D-103):** the first column is `format_version`, value `1` on every row, always written by the export. On import, a file without the column is read as version 1 (hand-made files); another value, or mixed values, is refused (`IMPORT_FORMAT_VERSION`).
- Header names are matched exactly. An unknown column is refused, and so is a duplicated one. Optional columns may be omitted.
- **Limits (D-101):** file ≤ 10 MB, at most 20,000 lines.

## Columns

| Column | Notes |
|---|---|
| `format_version` | `1` (see above) |
| `id` | transaction UUID; written by the export; optional on import (D-38) |
| `account` | app account name |
| `account_currency` | ISO 4217 cash currency of the account; used to create the account when it does not exist |
| `account_provider` | account provider (free text, e.g. "Directa"); used to create the account when it does not exist |
| `trade_date` | required |
| `trade_time` | optional, ordering only |
| `value_date` | optional settlement date (column, D-48) |
| `type` | `BUY`, `SELL`, `DIVIDEND`, `COUPON`, `DEPOSIT`, `WITHDRAWAL`, `STOCK_SPLIT` |
| `isin` | preferred asset identifier |
| `ticker`, `asset_name` | informational / fallback for asset search |
| `asset_type`, `asset_quote_currency`, `asset_quote_unit` | written by the export; on import used **only** to create an asset that does not exist yet (D-111); ignored for an existing asset (a mismatch is a warning) |
| `quantity` | nominal value for bonds |
| `unit_price` | in the asset quote unit (currency, minor unit such as GBX, or % of nominal for bonds) |
| `gross_amount`, `fees`, `accrued_interest_paid`, `accrued_interest_received`, `taxes`, `total_amount` | account currency; `taxes` signed |
| `split_ratio` | `new:old`, e.g. `2:1` |
| `affects_cash` | default `true` |
| `external_ref` | broker reference, used for duplicate detection |
| `notes` | free text (renamed from `note`, D-38) |
| `metadata` | JSON object (e.g. original broker movement type, D-13), quoted as one CSV field; may be empty |

Per-type required columns and invariants are those of `domain-rules.md` §2. Decimal strings follow the API limits: at most 15 integer and 20 decimal digits; quantities at most 8 decimals (D-98, D-68). A COUPON has no `quantity` (D-106). Export writes all columns, with empty cells where not applicable.

**Spreadsheet formula safety (D-110).** Only the free-text columns `notes`, `asset_name` and `metadata` are protected; numeric, date and code columns are never changed (a negative `taxes` stays `-2.44`). On export, a free-text cell that starts with `=`, `+`, `-` or `@`, or with `'` followed by one of them, gets one leading `'`. On import, one leading `'` is removed exactly when the rest of the cell would itself get one on export, i.e. it starts with `=`, `+`, `-` or `@` (or with `'` followed by one of them). So `=SUM(A1)` is exported as `'=SUM(A1)` and read back as `=SUM(A1)`; `'=x` is exported as `''=x` and read back as `'=x`; `'hello` is written and read unchanged; a hand-made `'=x` is read as `=x`.

## Export

`GET /api/v1/transactions/export.csv` (D-107) returns all of the user's transactions, or those of one account (`account_id`). They are sorted by account name, then in replay order (`domain-rules.md` §4), with every column above.

## Import behaviour

Same flow as every import (`requirements.md`): validate, identify assets (by ISIN, fallback ticker with user choice), show errors/ambiguities, preview, explicit confirmation. Preview and confirm are stateless (D-100).

- A row that breaks a format or field rule is listed as **invalid**, with its row number and every issue (stable codes such as `FIELD_INVALID` with the column and problem, `FIELD_COUNT`, `DUPLICATE_ID`); it **cannot be selected**.
- Every other row that is not a duplicate is selected by default.
- **"Do not move cash" option (D-112):** off, every row keeps its own `affects_cash` (empty = `true`); on, every BUY/SELL/DIVIDEND/COUPON row gets `affects_cash = false` whatever its cell says, and DEPOSIT/WITHDRAWAL rows are skipped by default (each selectable), as for Directa (§7 of `directa.md`).
- The selected rows are validated **as a whole** with the account's existing ledger: a quantity conflict refuses the confirmation and names the line (D-103). Negative cash, income on a never-held asset and future dates are warnings.

**Accounts.** In the preview each distinct `account` of the file is mapped to an existing account of the user, or created from `account`, `account_currency` and `account_provider`. Account names are not unique, so the user picks the target for each name; an existing account with the same name and currency is proposed. On a fresh instance (no accounts) all accounts are created from the file. A file with several accounts creates **one import batch per account**. A row whose `account_currency` differs from the currency of the mapped account is refused (the amounts are in the account currency, D-98).

**Assets.** A row's asset is the asset with that ISIN that the user can see. Without an ISIN, the preview offers the user's assets with that ticker for the user to choose. Otherwise the asset is created as a **private** asset (D-37) from `isin`, `asset_name`, `ticker` and the three `asset_*` columns. If those are missing, the user chooses them in the preview before confirming (as for Directa, D-102).

**Duplicates** (D-38), checked against the importing user's own transactions only:
1. by `id`: a row whose `id` already exists among the user's transactions is a duplicate;
2. otherwise by `external_ref` (when present): same account + `type` + `external_ref` is a duplicate.

Duplicates are skipped by default. A row without `id` gets a new UUID. If an `id` in the file already exists for **another** user, a new UUID is assigned silently: the import never reveals that the id exists. The same `id` twice in one file is refused with both line numbers. Transactions without `id` or `external_ref` cannot be matched; the manual-overlap notice of D-104 applies.

## Not in V1

Separate CSVs for derived data (positions, income): not in V1. Derived data is rebuilt from transactions (D-84).
