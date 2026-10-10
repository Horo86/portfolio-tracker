# App CSV format (import and export) — DRAFT

Status: draft, to be finalized in milestone M3. One documented format used both for the per-user **CSV export** and for **CSV import**, so that data can leave and re-enter the application (data ownership) and so that users of brokers without a dedicated importer have a path. Exporting and re-importing must reproduce the data without duplicates (D-38).

## Format

- UTF-8, comma separator, `"` quoting, header row, one transaction per row.
- Decimal separator `.`; no thousands separator; dates `YYYY-MM-DD`; times `HH:MM` (optional); booleans `true`/`false`.
- Rows may be in any order (the ledger is replayed deterministically, `domain-rules.md` §4).

## Columns

| Column | Notes |
|---|---|
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
| `quantity` | nominal value for bonds |
| `unit_price` | in the asset quote unit (currency, minor unit such as GBX, or % of nominal for bonds) |
| `gross_amount`, `fees`, `accrued_interest_paid`, `accrued_interest_received`, `taxes`, `total_amount` | account currency; `taxes` signed |
| `split_ratio` | `new:old`, e.g. `2:1` |
| `affects_cash` | default `true` |
| `external_ref` | broker reference, used for duplicate detection |
| `notes` | free text (renamed from `note`, D-38) |
| `metadata` | JSON object (e.g. original broker movement type, D-13), quoted as one CSV field; may be empty |

Per-type required columns and invariants are those of `domain-rules.md` §2. Export writes all columns, with empty cells where not applicable.

## Import behaviour

Same flow as every import (`requirements.md`): validate, identify assets (by ISIN, fallback ticker with user choice), show errors/ambiguities, preview, explicit confirmation. Rows violating the invariants or the ledger rules are reported with line numbers. The batch is validated as a whole.

**Accounts.** In the preview each distinct `account` of the file is mapped to an existing account of the user or created from `account`, `account_currency` and `account_provider`. On a fresh instance (no accounts) all accounts are created from the file. A file with several accounts creates **one import batch per account**.

**Duplicates** (D-38), checked against the importing user's own transactions only:
1. by `id`: a row whose `id` already exists among the user's transactions is a duplicate;
2. otherwise by `external_ref` (when present): same account + `type` + `external_ref` is a duplicate.

Duplicates are skipped by default. A row without `id` gets a new UUID. If an `id` in the file already exists for **another** user, a new UUID is assigned silently: the import never reveals that the id exists.

## Open points

- A format version marker (header convention) so that future changes remain readable.
- Whether the export also offers separate CSVs for derived data (positions, income) — not for V1 unless trivial.
