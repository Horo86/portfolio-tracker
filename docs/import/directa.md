# Import specification — Directa Excel export

Status: **partially verified.** Written from three real yearly exports (2022, 2023, 2024) of one Directa account, plus a fourth export of the same account that contains the first sales (§2a, structure only; imported since D-158). Those files are personal data and are **not** in this repository. This document describes only structure; all examples are synthetic. A synthetic fixture with the same structure is in `tests/fixtures/directa/`.

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
| `Vendita` | `SELL` (grouped by order reference, §3 "Sales", D-158) |
| `Rit. etf` | `taxes` of the SELL with the same order reference (file −x ⇒ taxes +x) |
| `Ritenuta su plusvalenza` | `taxes` of the one sale linked to it by date (§3 "Sales"); that sale is **"to review"** (D-158) |

### Not observed in the samples (do not implement from guesses)
A sale at a loss or any tax credit on a sale, a bond sale (see §2a for the full list); withdrawals (outgoing bank transfers); bond redemption/maturity; foreign-currency trades and foreign dividends; stock splits and other corporate actions; any other type. Until real samples exist these appear as **not recognized** or, inside a sale order, **ambiguous** (§3); the user enters them manually (a bond maturity is a SELL at 100% of nominal, D-19). Update this document when samples are available.

## 2a. Sales (observed in one export, structure only)

One real export of the account contains three sales of whole positions, all with a gain: two of instruments whose order carries a `Rit. etf` row (by its name, an ETF) and one with no tax row in its order and a separate `Ritenuta su plusvalenza` row. Each position had been bought earlier with `Acquisto` orders. Only structure is recorded here; the synthetic fixture `tests/fixtures/directa/directa_synthetic_sales_2025.xlsx` has the same shape.

Same as purchases (§1): header, metadata block, dates as `DD-MM-YYYY` text, numeric cells for quantity and amounts, `Importo Divisa` 0 and `Divisa` `EUR` on every row, rows of one order contiguous in the file.

**Sale order rows** (share one non-empty `Riferimento ordine`, same pattern as purchases: one letter and 13 digits):

| `Tipo operazione` | Rows per order | `Quantità` | `Importo euro` | Other cells |
|---|---|---|---|---|
| `Vendita` | exactly 1 in every observed order | > 0, whole number in the sample | > 0 (cash in) | ticker, ISIN, description filled; `Protocollo` blank text |
| `Commissioni` | 0 or 1 (1 of 3 orders had one) | 0 | < 0 | as `Vendita` |
| `Rit. etf` | 0 or 1 (2 of 3 orders) | 0 | < 0 (withheld) | as `Vendita` |

- All rows of a sale order have the same operation date, value date and ISIN; the value date is later than the operation date (settlement).
- File order inside an order: `Vendita`, then `Commissioni`, then `Rit. etf` (side-rows after the main row, as for purchases; attach by reference only, never by position, §3).
- The order's amounts sum to a positive number (net cash in).

**`Ritenuta su plusvalenza`** (capital gains tax not tied to an order):
- a standalone row: `Riferimento ordine`, ticker, ISIN and description are blank text; `Protocollo` filled (8 digits, as other standalone rows); `Quantità` 0; `Importo euro` < 0;
- its operation date and its value date are both **the value date of the sale it belongs to** (later than the sale's operation date); in the sample no other row has that operation date, and its protocol is not adjacent to any other row's protocol;
- the only link to its sale is therefore the date: it has no ISIN, no order reference and no consecutive protocol. In the sample it belongs, by elimination, to the one sale without a `Rit. etf`.

**Amounts.** The tax amounts are the broker's: a `Rit. etf` or `Ritenuta su plusvalenza` amount cannot always be reproduced from the rows of the file (cost basis, earlier losses and rates are the broker's), so the app records them as given and never recomputes them (hard rule 8).

**Still unknown (no real sample):**
- a sale at a loss: whether a tax credit appears (sign, row type: a positive `Rit. etf` or `Ritenuta su plusvalenza`, or another type) or no tax row at all;
- a bond sale: the accrued interest received and its withholding (row names and signs), and the tax row of a bond sale;
- a partially filled sale (several `Vendita` rows in one order): merged like a purchase (§3 "Sales"), a **rule derived from purchases, not yet confirmed by a real sale in several parts**; and a partial sale of a position;
- a sale with a fractional quantity;
- two sales settling on the same day, which would make a `Ritenuta su plusvalenza` row match more than one sale by date;
- a `Ritenuta su plusvalenza` in a different export from its sale (a sale at the end of December settles in January);
- a tax credit on its own (a positive row), foreign-currency sales, a Tobin tax on a sale.

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

**Sales** (D-158; the rows of §2a). An order whose main rows are `Vendita` is one SELL, merged like a purchase:
- several `Vendita` rows of one order are executions of one sale: quantities and amounts are summed (**rule derived from purchases, not yet confirmed by a real sale in several parts**); the unit price is then the weighted one;
- `Commissioni` and `Rit. etf` with that reference attach to it, by reference only;
- signs: every `Vendita` amount and quantity is positive; every `Commissioni` and `Rit. etf` amount is negative. A positive `Rit. etf` would be a tax credit, never observed: the order is ambiguous (`TAX_CREDIT_NOT_VERIFIED`); a zero fee or tax row breaks the field rule;
- `gross_amount = Σ Vendita`, `fees = −Σ Commissioni`, `taxes = −Σ Rit. etf` (recorded as given, never recomputed), `accrued_interest_received` none, `total_amount = gross − fees − taxes = Σ all amounts of the order` (this equality must hold; with the signs above it holds by construction);
- `quantity = Σ quantity`; `unit_price = gross_amount / quantity`; dates and `external_ref` as for purchases;
- a sale of a bond is ambiguous (`BOND_SALE_NOT_VERIFIED`): its rows (accrued interest received, its withholding) were never seen. A bond is known by the file's evidence (§5) or by the user's own asset of type `BOND`;
- an order mixing `Acquisto` and `Vendita` rows is ambiguous; so is a side-row never seen with that kind of order (`Ratei pass.obb.`, `Rit.ratei pass.obb.` or `Rit.credito disaggio` in a sale, `Rit. etf` in a purchase).

**Capital gains tax on its own row.** A `Ritenuta su plusvalenza` row (no order reference, no ISIN; §2a) is linked to a sale when **exactly one sale order** of the file (an order, however many `Vendita` rows it has) has a value date equal to both the tax row's operation date and its value date, and has no `Rit. etf` row. Then:
- the tax is added to that sale (`taxes += −row`, `total_amount += row`), its row becomes one of the sale's rows, and its protocol is kept in the sale's metadata (`linked_tax_protocol`);
- the link is **inferred** ("imposta collegata per data", `SALE_TAX_LINKED_BY_DATE`): the sale is **"to review"**, not selected by default, until a second real example confirms the rule. The preview's "Da rivedere" tab has a tick box on each line: the user includes it with one click (D-158).

A `Ritenuta su plusvalenza` row that cannot be linked is **ambiguous**, never imported (never as a withdrawal): no sale order settles on its date (`SALE_TAX_NO_SALE`), several do (`SALE_TAX_SEVERAL_SALES`), another tax row claims the same sale (`SALE_TAX_SHARED`), or its sale is not importable (`SALE_TAX_SALE_REJECTED`). The preview then shows a notice with its date and amount (`SALE_TAX_TO_ADD_BY_HAND`): the user adds it by hand to the right sale, whose net gain does not include it until then. A positive row (a credit) or a zero row is ambiguous too, without linking. A row rejected by its own checks (these, another currency, a missing or repeated protocol, a quantity, a cell that cannot be read, or inside an order) is never linked, but a sale it may belong to is not imported with taxes 0 either (D-160): every importable sale settling on one of its dates, or on any date when none can be read, is **ambiguous** (`SALE_TAX_ROW_REJECTED`), and the row gets the notice when its date and amount can be read. Sale and tax are then entered by hand.

**Order of the files.** A sale needs its purchases in the account: import the files oldest first. A file whose sale comes before its purchases is refused at the preview with a quantity conflict (`QUANTITY_CONFLICT`, "importa prima i file più vecchi").

**Income.** A `Cedola obb.` / `Incasso dividendi italia` row and its withholding row (`Rit.cedola obb.` / `Ritenuta dividendi italia`) have the **same operation date and ISIN** and **consecutive protocol numbers** (withholding = income + 1). Pair them by that rule; if there is no consecutive protocol but exactly one candidate with the same date and ISIN, pair it and mark the pairing as inferred; otherwise flag an ambiguity. Income with no withholding row: `taxes = 0`.
- `gross_amount = income row`, `taxes = −withholding row`, `total_amount = gross − taxes`, `value_date` = `Data valuta`, `external_ref` = income row protocol.

**Other rows.** `Conferimento con bonifico`, `Bollo portafoglio titoli*`, `Tobin tax italia`: one transaction per row, `value_date` = `Data valuta`, `external_ref` = protocol. (The Tobin tax appears as a separate row without instrument or order; it cannot be attributed to a trade.)

**Ambiguities, never imported (D-108).** Listed with their rows and not selectable:
- one order reference with different operation dates, value dates or ISINs;
- a side-row whose order has no main row (no `Acquisto` or `Vendita`) in the file;
- the sale rules above: an order mixing purchases and sales, a side-row not seen with its kind of order, a tax credit, a bond sale, a `Ritenuta su plusvalenza` that cannot be linked;
- an order containing a row of an unrecognized type;
- the same protocol twice in one file;
- an order whose amounts break a field rule (every `Acquisto` amount must be negative, every `Vendita` amount positive);
- a dividend or coupon whose withholding row may have been rejected (same kind, date and ISIN as far as readable) and that is not paired by consecutive protocol (`PAIRED_ROW_REJECTED`): it would be imported with taxes 0;
- the number guards of §1.

An unrecognized row without an order reference stays simply "not recognized".

## 4. Account association

The first import asks which app account the file belongs to and remembers the Directa account code ↔ app account link (confirming stores the code on an account that has none; later files go to that account unless the user picks another, D-113). If a later file has a different account code, warn before proceeding. The target account must have cash currency EUR: associating a Directa file with an account in another currency is a **blocking error** (D-49).

## 5. Assets

Identified by ISIN. For each new ISIN the preview proposes metadata from the market data provider and an asset type (`ETF`/`STOCK`/`BOND`) for the user to confirm. If the provider cannot resolve the ISIN, the asset is still created from file data (ISIN, name) without prices (valued at cost, see `domain-rules.md` §12). Bond prices from free providers are poor: manual prices are expected for many.

**Until providers exist (M3, D-102):** an ISIN matches an asset the user can already see; otherwise the preview proposes a new **private** asset from the ISIN and the description. Type `BOND` is proposed only on evidence: a ticker starting with `M.`, or a `Cedola obb.` / `Ratei pass.obb.` / `Rit.ratei pass.obb.` row for that ISIN. Otherwise no type is proposed, and the user must choose before confirming. Quote currency EUR; quote unit from the type. The BUY `unit_price` follows the chosen type (§3).

## 6. Duplicates and re-import

Identity = (account, `external_ref`, type). Rows already present are listed as "already imported" and are never imported again (not selectable, D-113); re-importing a file must import nothing new. A linked `Ritenuta su plusvalenza` row is one of its sale's rows: it is already present with its sale (its protocol is in the sale's metadata). Files may overlap or be exported again (D-160): a line is already present only with the stored figures (operation date, quantity, gross, fees, taxes, total; only the figures, so a tax added by hand equal to the linked one counts as present). Otherwise it is **ambiguous**, "already present with different amounts: check by hand", with a notice counting such lines; nothing is skipped silently, imported twice or written over. Two cases this catches: a sale exported before its capital gains tax was charged, then again with its tax (the notice of a tax to add by hand is shown too); an order cut off by the 3000-row limit (§1), whose next file has every execution. Known limit: a transaction edited after its import shows as "different amounts" whenever its file is imported again. Yearly files are contiguous; import them oldest first ("purchases first", D-158): a file with only purchases, dividends and expenses imports in any order (the ledger is replayed by date), but a sale before its purchases is a quantity conflict (§3).

Transactions entered by hand have no `external_ref` and cannot be matched. When the target account has such transactions inside the file's date range, the preview shows one notice with their count and range (`MANUAL_OVERLAP`, D-104); there is no per-row matching.

## 7. Preview and options

Before confirmation show: counts (to import / duplicates / not recognized / warnings), new assets, merged orders, inferred pairings, rows breaking the amount invariant, notices (possible truncation at 3000 rows; negative cash at some date; a capital gains tax to add by hand, with its date, D-158; lines already present with different amounts, D-160). Option: "I did not record the deposits: do not move cash" (D-49):
- sets `affects_cash = false` on imported BUY/SELL/DIVIDEND/COUPON;
- **skips by default** the `DEPOSIT` rows and the account-expense `WITHDRAWAL` rows (stamp duty, Tobin tax); each of them can be selected individually in the preview to import it anyway.

Without the option, account-level expenses are shown as a group, pre-selected to be imported as withdrawals, with the option to exclude them.

Preview categories returned by the API (the screens group them, mockup 08):
- **to import** (selected);
- **already present** (duplicates, skipped, not selectable, D-113);
- **not recognized**;
- **to review** (D-99, not selected, selectable; also a sale with its capital gains tax linked by date, D-158);
- **ambiguous** (D-108, not selectable; also a line already present with different amounts, D-160);
- **skipped by choice** (the option above, selectable).

Warnings and notices are listed separately. Preview and confirm are stateless (D-100): confirm re-sends the file with the selection and the preview fingerprint.

## 8. Ordering

Files are descending by date. The importer assigns insertion sequence in reverse file order (oldest first) and the ledger uses the deterministic same-day ordering of `domain-rules.md` §4 (Directa rows have no time). A merged transaction (several rows of one order, or income plus withholding) takes the sequence of its **oldest row in reverse file order**, i.e. its last row in the file (D-49). Example from the fixture: orders T5 and T6 on the same day are replayed T6 then T5.

## 9. Tests

- Synthetic fixture: `tests/fixtures/directa/directa_synthetic_2024.xlsx` + `directa_synthetic_2024.expected.json` (invented data, fictitious ISINs with valid check digits). Covers: partial executions, same-day identical orders, bond purchase with side-rows, dividend and coupon with withholding, account expenses (literal `*` in the stamp-duty type), an unrecognized row, deposits, file with side-rows listed after the main row. The expected JSON lists all transactions in replay order and the "do not move cash" scenario. The bond order T0000000000004 contains `Rit.credito disaggio`, so with the default selection it is "to review" (11 transactions to import). The 12-transaction outcome is the result after the user selects it (D-99).
- Sales fixture: `tests/fixtures/directa/directa_synthetic_sales_2025.xlsx` + `directa_synthetic_sales_2025.expected.json` (invented, `ZZ` ISINs): the three sale shapes of §2a, an invented sale in two executions, and the purchases they close; 18 rows → 9 transactions. The share sale has its `Ritenuta su plusvalenza` linked by date, so by default it is "to review" (8 transactions to import); the expected JSON gives both outcomes, the realized gains and the "do not move cash" option.
- Synthetic tests built in code cover what the fixture does not: float artefacts in numeric cells, non-EUR rows, the ambiguities of §3, and the size limits.
- Optional local tests on real files: put them under `private/directa/` (git-ignored). Tests that need them must skip when the folder is empty. Never copy real figures into the repository, issues, or documentation.

## 10. Known limitations to document in the UI

Sales are imported, except a sale at a loss or with a tax credit, a bond sale, an order mixing purchases and sales, and foreign currency; withdrawals, bond redemptions and corporate actions are not imported yet. A sale with its capital gains tax linked by date is "to review"; a tax that cannot be linked is added by hand. Import the files oldest first. Stamp duty and Tobin tax do not influence returns. Bond valuation uses clean prices.
