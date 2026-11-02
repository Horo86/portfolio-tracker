# Directa synthetic fixture

`directa_synthetic_2024.xlsx` reproduces the **structure** of a Directa movements export (metadata block, header on row 10, rows in descending date order, order references, protocol numbers) with **entirely invented data**: fictitious account `B0000 Test User`, fictitious ISINs with valid check digits (`IE0TEST00020`, `IT0TEST00015`, `IT0TEST00031`), invented amounts.

`directa_synthetic_2024.expected.json` is the expected outcome of importing it (verified by an independent calculation): 22 rows → 12 transactions + 1 unrecognized row, listed in replay order with insertion sequence (`seq`), `external_ref`, `value_date`, amounts and running cash; final cash 983.05; positions; merged orders; income; account expenses imported as withdrawals; the "do not move cash" option with its default selection (8 transactions, deposits and expenses skipped, cash 0.00); duplicate detection. The bond order contains `Rit.credito disaggio`, whose meaning is not confirmed: by default it is "to review" and not selected (D-99), so the default import is 11 transactions (cash 5872.95; with the option, 7). The 12-transaction figures above are the result after the user selects it.

It exercises: partial executions of one order, two different same-day orders for the same asset, a bond bought with accrued interest and tax-credit side-rows, dividend and coupon with withholding, stamp duty (type name with a literal `*`) and Tobin tax, deposits, one row of an unrecognized type, and same-day ordering of a BUY and a WITHDRAWAL.

Not covered because no real sample exists yet: withdrawals, bond redemptions, foreign currency, corporate actions (see `docs/import/directa.md`). Also not covered: an `Acquisto` row without order reference (rule in `directa.md` §3) and the blocking error for a non-EUR account (§4), which belongs to the account association step rather than to the file.

## Sales fixture

`directa_synthetic_sales_2025.xlsx` reproduces the **structure** of the first real export with sales (`docs/import/directa.md` §2a) with invented data: account `B0000 Test User`, ISINs with the non-existent country code `ZZ` and valid check digits (`ZZ00TE5TET05` with an alphanumeric national code, `ZZ0000000024`, `ZZ0000000032`, `ZZ0000000040`), invented names, tickers and amounts. 18 rows, descending by date:

- a deposit and four purchases (two with `Commissioni`) that open the positions sold later;
- an ETF sold with `Vendita` + `Rit. etf` (no fee row);
- an ETF sold with `Vendita` + `Commissioni` + `Rit. etf`;
- an ETF sold in two executions (two `Vendita` rows of one order) + `Commissioni` + `Rit. etf`: invented, since no real sale in several parts has been seen (D-158 rule 1);
- a share sold with `Vendita` alone, and its `Ritenuta su plusvalenza` as a standalone row (protocol, no ISIN, no order reference) dated on the sale's value date, with a non-adjacent protocol;
- blank text cells (not empty cells) for the missing ticker, ISIN, description and protocol, and a space-padded empty order reference, as in the real file.

Every sale closes the whole position with a gain; the invented taxes are 26% of the gain so the numbers are easy to check by hand (real tax amounts do not always reconcile and are recorded as given).

`directa_synthetic_sales_2025.expected.json` is its expected outcome, computed by hand: 18 rows → 9 transactions in replay order with running cash (final 2217.71), the realized gains of each security, the merged sale, the linked tax. The share sale has its tax linked by date, so by default it is "to review" (D-158): the default import is 8 transactions (cash 1879.18, the share still held); the 9-transaction figures are the result after the user ticks it.

Not covered (no real sample): a sale at a loss or a tax credit, a bond sale, a partial sale of a position, two sales settling on the same day, a tax row in a different file from its sale (rules built in code cover the ambiguous cases).

**Never add real exports here.** Real files go in `/private/` (git-ignored).
