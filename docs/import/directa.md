# Import specification — Directa Excel export

Status: **partially verified.** Written from three real yearly exports (2022, 2023, 2024) of one Directa account. Those files are personal data and are **not** in this repository. This document describes only structure; all examples are synthetic. A synthetic fixture with the same structure is in `tests/fixtures/directa/`.

Rule for implementers: **do not guess.** Only the movement types in "Verified" below are recognized. Everything else is reported as "not recognized" in the preview and not imported.

## 1. File structure (verified)

- Excel workbook `.xlsx`, first sheet used. Sheet name pattern: `Movimenti_<account>_<d-m-yyyy>` (informational).
- Rows 1–8 are metadata (strings in column A). Do not rely on fixed positions; locate the header by its first cell `Data operazione`.
  - `Conto : <account code> <holder name>` — extract only the account code (e.g. `B0000`). **Never store or log the holder name** (personal data).
  - `Data estrazione : …`, `Tutti i movimenti ordinati per Data Operazione`, `Dal : dd-mm-yyyy`, `al : dd-mm-yyyy`.
  - `Il file include i primi 3000 movimenti` — if the file has 3000 data rows, warn that it may be truncated.
- Header row (verified): `Data operazione | Data valuta | Tipo operazione | Ticker | Isin | Protocollo | Descrizione | Quantità | Importo euro | Importo Divisa | Divisa | Riferimento ordine`.
- Data rows are sorted by operation date **descending**. Dates are strings `DD-MM-YYYY`; amounts and quantities are numeric cells. `Riferimento ordine` and `Protocollo` can be empty strings or whitespace-padded; trim them.
- Sign convention: `Importo euro` is the cash movement of that row (negative = cash out).
- Instrument descriptions are truncated (~30 characters) and **can change between exports for the same ISIN**: the ISIN is the key, the description is informational.
- `Ticker` for listed instruments is a market symbol; for Italian government bonds it is a broker-internal code (prefix `M.`), **not** a market symbol: never use it for price lookup.
- `Importo Divisa` / `Divisa`: in all verified rows 0 / `EUR`. Rows with a non-EUR currency or non-zero foreign amount have **not been observed**: treat as not recognized.

## 2. Movement types

### Verified
| Directa `Tipo operazione` | App mapping |
|---|---|
| `Acquisto` | `BUY` (grouped by order reference, see §3) |
| `Commissioni` | `fees` of the BUY with the same order reference |
| `Ratei pass.obb.` | `accrued_interest_paid` of the BUY with the same order reference |
| `Rit.ratei pass.obb.` | credit → added to `taxes` of that BUY with inverted sign (file +x ⇒ taxes −x) |
| `Rit.credito disaggio` | credit → same as above. **Meaning to be confirmed with the broker** (assumed a tax credit related to issue discount) |
| `Cedola obb.` | `COUPON` (gross) |
| `Rit.cedola obb.` | `taxes` of the paired COUPON (file −x ⇒ taxes +x) |
| `Incasso dividendi italia` | `DIVIDEND` (gross) |
| `Ritenuta dividendi italia` | `taxes` of the paired DIVIDEND (file −x ⇒ taxes +x) |
| `Conferimento con bonifico` | `DEPOSIT` |
| `Bollo portafoglio titoli*` | `WITHDRAWAL`, note = original type, original type stored in metadata (D-13). The `*` is a **literal character** of the type name, not a wildcard (D-49). |
| `Tobin tax italia` | `WITHDRAWAL`, note = original type, original type stored in metadata (D-13) |

### Not observed in the samples (do not implement from guesses)
Sales and their capital gains tax; withdrawals (outgoing bank transfers); bond redemption/maturity; foreign-currency trades and foreign dividends; stock splits and other corporate actions; any other type. Until real samples exist these appear as **not recognized**; the user enters them manually (a bond maturity is a SELL at 100% of nominal, D-19). Update this document when samples are available.

## 3. Grouping and pairing rules (verified)

**Trades.** Rows sharing the same non-empty `Riferimento ordine` form one transaction. An `Acquisto` row with an empty `Riferimento ordine` is **not recognized** (D-49).
- One order may contain **several `Acquisto` rows** (partial executions): merge them — quantity and amounts are summed.
- `Commissioni`, `Ratei pass.obb.`, `Rit.ratei pass.obb.`, `Rit.credito disaggio` with that reference attach to the merged BUY. Attach by reference only, **never by row position** (inverting the file order puts side-rows before the main row).
- Two different orders can have identical date, ISIN and quantity (and nearly identical amounts): never use these as identity.

Resulting BUY (all in EUR):
- `gross_amount = −Σ Acquisto`, `fees = −Σ Commissioni`, `accrued_interest_paid = −Σ Ratei`, `taxes = −(Σ Rit.ratei + Σ Rit.credito disaggio)`, `total_amount = gross + fees + accrued + taxes = −Σ all amounts of the order` (this equality must hold).
- `quantity = Σ quantity` (nominal value for bonds).
- `unit_price = gross_amount / quantity` (stocks/ETFs); `gross_amount / quantity × 100` (bonds, % of nominal).
- `trade_date` = `Data operazione`; `value_date` = `Data valuta` (transaction column, D-48); `external_ref` = order reference.

**Income.** A `Cedola obb.` / `Incasso dividendi italia` row and its withholding row (`Rit.cedola obb.` / `Ritenuta dividendi italia`) have the **same operation date and ISIN** and **consecutive protocol numbers** (withholding = income + 1). Pair them by that rule; if there is no consecutive protocol but exactly one candidate with the same date and ISIN, pair it and mark the pairing as inferred; otherwise flag an ambiguity. Income with no withholding row: `taxes = 0`.
- `gross_amount = income row`, `taxes = −withholding row`, `total_amount = gross − taxes`, `value_date` = `Data valuta`, `external_ref` = income row protocol.

**Other rows.** `Conferimento con bonifico`, `Bollo portafoglio titoli*`, `Tobin tax italia`: one transaction per row, `value_date` = `Data valuta`, `external_ref` = protocol. (The Tobin tax appears as a separate row without instrument or order; it cannot be attributed to a trade.)

## 4. Account association

The first import asks which app account the file belongs to and remembers the Directa account code ↔ app account link. If a later file has a different account code, warn before proceeding. The target account must have cash currency EUR: associating a Directa file with an account in another currency is a **blocking error** (D-49).

## 5. Assets

Identified by ISIN. For each new ISIN the preview proposes metadata from the market data provider and an asset type (`ETF`/`STOCK`/`BOND`) for the user to confirm. If the provider cannot resolve the ISIN, the asset is still created from file data (ISIN, name) without prices (valued at cost, see `domain-rules.md` §12). Bond prices from free providers are poor: manual prices are expected for many.

## 6. Duplicates and re-import

Identity = (account, `external_ref`, type). Rows already present are listed as "already imported" and skipped by default; re-importing a file must import nothing new. Yearly files are contiguous and non-overlapping; they can be imported in any order (the ledger is replayed by date).

## 7. Preview and options

Before confirmation show: counts (to import / duplicates / not recognized / warnings), new assets, merged orders, inferred pairings, rows breaking the amount invariant, notices (possible truncation at 3000 rows; negative cash at some date). Option: "I did not record the deposits: do not move cash" (D-49):
- sets `affects_cash = false` on imported BUY/SELL/DIVIDEND/COUPON;
- **skips by default** the `DEPOSIT` rows and the account-expense `WITHDRAWAL` rows (stamp duty, Tobin tax); each of them can be selected individually in the preview to import it anyway.

Without the option, account-level expenses are shown as a group, pre-selected to be imported as withdrawals, with the option to exclude them.

## 8. Ordering

Files are descending by date. The importer assigns insertion sequence in reverse file order (oldest first) and the ledger uses the deterministic same-day ordering of `domain-rules.md` §4 (Directa rows have no time). A merged transaction (several rows of one order, or income plus withholding) takes the sequence of its **oldest row in reverse file order**, i.e. its last row in the file (D-49). Example from the fixture: orders T5 and T6 on the same day are replayed T6 then T5.

## 9. Tests

- Synthetic fixture: `tests/fixtures/directa/directa_synthetic_2024.xlsx` + `directa_synthetic_2024.expected.json` (invented data, fictitious ISINs with valid check digits). Covers: partial executions, same-day identical orders, bond purchase with side-rows, dividend and coupon with withholding, account expenses (literal `*` in the stamp-duty type), an unrecognized row, deposits, file with side-rows listed after the main row. The expected JSON lists all transactions in replay order and the "do not move cash" scenario.
- Optional local tests on real files: put them under `private/directa/` (git-ignored). Tests that need them must skip when the folder is empty. Never copy real figures into the repository, issues, or documentation.

## 10. Known limitations to document in the UI

Sales, withdrawals, redemptions, foreign currency and corporate actions are not imported yet. Stamp duty and Tobin tax do not influence returns. Bond valuation uses clean prices.
