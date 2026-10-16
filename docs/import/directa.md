# Import specification — Directa Excel export

Status: **partially verified.** Written from three real yearly exports (2022, 2023, 2024) of one Directa account. Those files are personal data and are **not** in this repository. This document describes only structure; all examples are synthetic. A synthetic fixture with the same structure is in `tests/fixtures/directa/`.

Rule for implementers: **do not guess.** Only the movement types in "Verified" below are recognized. Everything else is reported as "not recognized" in the preview and not imported. A verified type whose meaning is not confirmed puts its transaction under "to review" (D-99); inconsistent rows are ambiguities and are never imported (D-108).

## 1. File structure (verified)

- Excel workbook `.xlsx`, first sheet used. Sheet name pattern: `Movimenti_<account>_<d-m-yyyy>` (informational).
- Rows 1–8 are metadata (strings in column A). Do not rely on fixed positions; locate the header by its first cell `Data operazione`.
  - `Conto : <account code> <holder name>` — extract only the account code (e.g. `B0000`). **Never store or log the holder name** (personal data). The code must contain a digit: a first word without one is taken as part of the name and no code is stored (the user picks the account).
  - `Data estrazione : …`, `Tutti i movimenti ordinati per Data Operazione`, `Dal : dd-mm-yyyy`, `al : dd-mm-yyyy`.
  - `Il file include i primi 3000 movimenti` — if the file has 3000 data rows, warn that it may be truncated.
- Header row (verified): `Data operazione | Data valuta | Tipo operazione | Ticker | Isin | Protocollo | Descrizione | Quantità | Importo euro | Importo Divisa | Divisa | Riferimento ordine`.
- Data rows are sorted by operation date **descending**. Dates are strings `DD-MM-YYYY`; amounts and quantities are numeric cells. `Riferimento ordine` and `Protocollo` can be empty strings or whitespace-padded; trim them.
- Sign convention: `Importo euro` is the cash movement of that row (negative = cash out).
- Instrument descriptions are truncated (~30 characters) and **can change between exports for the same ISIN**: the ISIN is the key, the description is informational.
- `Ticker` for listed instruments is a market symbol; for Italian government bonds it is a broker-internal code (prefix `M.`), **not** a market symbol: never use it for price lookup.
- `Importo Divisa` / `Divisa`: in all verified rows 0 / `EUR`. Rows with a non-EUR currency or non-zero foreign amount have **not been observed**: treat as not recognized.
- **Numeric cells are binary doubles** in the real exports (e.g. an amount of 90.41 can be stored as `90.409999999999997`). Convert each to the shortest decimal string that round-trips to the same double, then to `Decimal`; no other rounding (D-105). After conversion an `Importo euro` with more than 2 decimals (this check is for the euro amount column only), a `Quantità` with more than 8 (D-68), or a text cell where a number is expected makes the row ambiguous; so do the API limits (15 integer digits, codes of at most 64 characters, valid ISINs, ticker 32 / description 200 / movement type 100 characters; D-105). A non-zero `Quantità` is expected only on the main row of an order (`Acquisto`; `Vendita` and splits once verified). On side-rows, income and withholding rows, deposits and account expenses it must be 0, otherwise the row is ambiguous (D-106).
- **Limits** (D-101): file ≤ 10 MB; at most 100 zip parts; declared uncompressed size ≤ 16 MB for the sheet, 4 MB for shared strings, 1 MB for every other part, 24 MB in total; a streaming pre-scan refuses rows wider than 64 cells; reading stops at 10,000 data rows; one file parsed at a time. The xlsx is read with openpyxl in read-only mode, hardened with defusedxml.

## 2. Movement types

### Verified
| Directa `Tipo operazione` | App mapping |
|---|---|
| `Acquisto` | `BUY` (grouped by order reference, see §3) |
| `Commissioni` | `fees` of the BUY with the same order reference |
| `Ratei pass.obb.` | `accrued_interest_paid` of the BUY with the same order reference |
| `Rit.ratei pass.obb.` | credit → added to `taxes` of that BUY with inverted sign (file +x ⇒ taxes −x) |
| `Rit.credito disaggio` | credit → same as above. **Meaning to be confirmed with the broker** (assumed a tax credit related to issue discount). Until confirmed, the BUY it belongs to is **"to review", not selected by default** (D-99; reason code `UNCONFIRMED_MOVEMENT_TYPE`, mapped as `TAX_CREDIT`) |
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

**Ambiguities, never imported (D-108).** Listed with their rows and not selectable:
- one order reference with different operation dates, value dates or ISINs;
- a side-row whose order has no main row (no `Acquisto` or `Vendita`) in the file; a `Vendita` main row is itself not recognized until verified, so such an order is ambiguous as a whole;
- an order containing a row of an unrecognized type;
- the same protocol twice in one file;
- an order whose amounts break a field rule (every `Acquisto` amount must be negative);
- a dividend or coupon whose withholding row may have been rejected (same kind, date and ISIN as far as readable) and that is not paired by consecutive protocol (`PAIRED_ROW_REJECTED`): it would be imported with taxes 0;
- the number guards of §1.

An unrecognized row without an order reference stays simply "not recognized".

## 4. Account association

The first import asks which app account the file belongs to and remembers the Directa account code ↔ app account link. If a later file has a different account code, warn before proceeding. The target account must have cash currency EUR: associating a Directa file with an account in another currency is a **blocking error** (D-49).

## 5. Assets

Identified by ISIN. For each new ISIN the preview proposes metadata from the market data provider and an asset type (`ETF`/`STOCK`/`BOND`) for the user to confirm. If the provider cannot resolve the ISIN, the asset is still created from file data (ISIN, name) without prices (valued at cost, see `domain-rules.md` §12). Bond prices from free providers are poor: manual prices are expected for many.

**Until providers exist (M3, D-102):** an ISIN matches an asset the user can already see; otherwise the preview proposes a new **private** asset from the ISIN and the description. Type `BOND` is proposed only on evidence: a ticker starting with `M.`, or a `Cedola obb.` / `Ratei pass.obb.` / `Rit.ratei pass.obb.` row for that ISIN. Otherwise no type is proposed, and the user must choose before confirming. Quote currency EUR; quote unit from the type. The BUY `unit_price` follows the chosen type (§3).

## 6. Duplicates and re-import

Identity = (account, `external_ref`, type). Rows already present are listed as "already imported" and skipped by default; re-importing a file must import nothing new. Yearly files are contiguous and non-overlapping; they can be imported in any order (the ledger is replayed by date).

Transactions entered by hand have no `external_ref` and cannot be matched. When the target account has such transactions inside the file's date range, the preview shows one notice with their count and range (`MANUAL_OVERLAP`, D-104); there is no per-row matching.

## 7. Preview and options

Before confirmation show: counts (to import / duplicates / not recognized / warnings), new assets, merged orders, inferred pairings, rows breaking the amount invariant, notices (possible truncation at 3000 rows; negative cash at some date). Option: "I did not record the deposits: do not move cash" (D-49):
- sets `affects_cash = false` on imported BUY/SELL/DIVIDEND/COUPON;
- **skips by default** the `DEPOSIT` rows and the account-expense `WITHDRAWAL` rows (stamp duty, Tobin tax); each of them can be selected individually in the preview to import it anyway.

Without the option, account-level expenses are shown as a group, pre-selected to be imported as withdrawals, with the option to exclude them.

Preview categories returned by the API (the screens group them, mockup 08):
- **to import** (selected);
- **already present** (duplicates, skipped);
- **not recognized**;
- **to review** (D-99, not selected, selectable);
- **ambiguous** (D-108, not selectable);
- **skipped by choice** (the option above, selectable).

Warnings and notices are listed separately. Preview and confirm are stateless (D-100): confirm re-sends the file with the selection and the preview fingerprint.

## 8. Ordering

Files are descending by date. The importer assigns insertion sequence in reverse file order (oldest first) and the ledger uses the deterministic same-day ordering of `domain-rules.md` §4 (Directa rows have no time). A merged transaction (several rows of one order, or income plus withholding) takes the sequence of its **oldest row in reverse file order**, i.e. its last row in the file (D-49). Example from the fixture: orders T5 and T6 on the same day are replayed T6 then T5.

## 9. Tests

- Synthetic fixture: `tests/fixtures/directa/directa_synthetic_2024.xlsx` + `directa_synthetic_2024.expected.json` (invented data, fictitious ISINs with valid check digits). Covers: partial executions, same-day identical orders, bond purchase with side-rows, dividend and coupon with withholding, account expenses (literal `*` in the stamp-duty type), an unrecognized row, deposits, file with side-rows listed after the main row. The expected JSON lists all transactions in replay order and the "do not move cash" scenario. The bond order T0000000000004 contains `Rit.credito disaggio`, so with the default selection it is "to review" (11 transactions to import). The 12-transaction outcome is the result after the user selects it (D-99).
- Synthetic tests built in code cover what the fixture does not: float artefacts in numeric cells, non-EUR rows, the ambiguities of §3, and the size limits.
- Optional local tests on real files: put them under `private/directa/` (git-ignored). Tests that need them must skip when the folder is empty. Never copy real figures into the repository, issues, or documentation.

## 10. Known limitations to document in the UI

Sales, withdrawals, redemptions, foreign currency and corporate actions are not imported yet. Stamp duty and Tobin tax do not influence returns. Bond valuation uses clean prices.
