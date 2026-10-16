# Directa synthetic fixture

`directa_synthetic_2024.xlsx` reproduces the **structure** of a Directa movements export (metadata block, header on row 10, rows in descending date order, order references, protocol numbers) with **entirely invented data**: fictitious account `B0000 Test User`, fictitious ISINs with valid check digits (`IE0TEST00020`, `IT0TEST00015`, `IT0TEST00031`), invented amounts.

`directa_synthetic_2024.expected.json` is the expected outcome of importing it (verified by an independent calculation): 22 rows → 12 transactions + 1 unrecognized row, listed in replay order with insertion sequence (`seq`), `external_ref`, `value_date`, amounts and running cash; final cash 983.05; positions; merged orders; income; account expenses imported as withdrawals; the "do not move cash" option with its default selection (8 transactions, deposits and expenses skipped, cash 0.00); duplicate detection. The bond order contains `Rit.credito disaggio`, whose meaning is not confirmed: by default it is "to review" and not selected (D-99), so the default import is 11 transactions (cash 5872.95; with the option, 7). The 12-transaction figures above are the result after the user selects it.

It exercises: partial executions of one order, two different same-day orders for the same asset, a bond bought with accrued interest and tax-credit side-rows, dividend and coupon with withholding, stamp duty (type name with a literal `*`) and Tobin tax, deposits, one row of an unrecognized type, and same-day ordering of a BUY and a WITHDRAWAL.

Not covered because no real sample exists yet: sales, withdrawals, bond redemptions, foreign currency, corporate actions (see `docs/import/directa.md`). Also not covered: an `Acquisto` row without order reference (rule in `directa.md` §3) and the blocking error for a non-EUR account (§4), which belongs to the account association step rather than to the file.

**Never add real exports here.** Real files go in `/private/` (git-ignored).
