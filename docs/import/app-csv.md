# App CSV format (import and export) — DRAFT

Status: draft, to be finalized in milestone M3. One documented format used both for the per-user **CSV export** and for **CSV import**, so that data can leave and re-enter the application (data ownership) and so that users of brokers without a dedicated importer have a path.

## Format

- UTF-8, comma separator, `"` quoting, header row, one transaction per row.
- Decimal separator `.`; no thousands separator; dates `YYYY-MM-DD`; times `HH:MM` (optional); booleans `true`/`false`.
- Rows may be in any order (the ledger is replayed deterministically, `domain-rules.md` §4).

## Columns

| Column | Notes |
|---|---|
| `account` | app account name (the import maps it to an account in the preview) |
| `trade_date` | required |
| `trade_time` | optional, ordering only |
| `value_date` | optional |
| `type` | `BUY`, `SELL`, `DIVIDEND`, `COUPON`, `DEPOSIT`, `WITHDRAWAL`, `STOCK_SPLIT` |
| `isin` | preferred asset identifier |
| `ticker`, `asset_name` | informational / fallback for asset search |
| `quantity` | nominal value for bonds |
| `unit_price` | in the asset quote unit (currency, or % of nominal for bonds) |
| `gross_amount`, `fees`, `accrued_interest_paid`, `taxes`, `total_amount` | account currency; `taxes` signed |
| `split_ratio` | `new:old`, e.g. `2:1` |
| `affects_cash` | default `true` |
| `external_ref` | broker reference, used for duplicate detection |
| `note` | free text |

Per-type required columns and invariants are those of `domain-rules.md` §2. Export writes all columns, with empty cells where not applicable.

## Import behaviour

Same flow as every import (`requirements.md`): validate, identify assets (by ISIN, fallback ticker with user choice), show errors/ambiguities, preview, explicit confirmation. Duplicates: same `account` + `type` + `external_ref` (when present). Rows violating the invariants or the ledger rules are reported with line numbers. The batch is validated as a whole.

## Open points

- A format version marker (header convention) so that future changes remain readable.
- Whether the export also offers separate CSVs for derived data (positions, income) — not for V1 unless trivial.
