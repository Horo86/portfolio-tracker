# Directa synthetic fixture

`directa_synthetic_2024.xlsx` reproduces the **structure** of a Directa movements export (metadata block, header on row 10, rows in descending date order, order references, protocol numbers) with **entirely invented data**: fictitious account `B0000 Test User`, fictitious ISINs with valid check digits (`IE0TEST00020`, `IT0TEST00015`, `IT0TEST00031`), invented amounts.

`directa_synthetic_2024.expected.json` is the expected outcome of importing it (verified by an independent calculation): 22 rows → 12 transactions + 1 unrecognized row; final cash 983.05; positions; merged orders; income; account expenses imported as withdrawals; duplicate detection.

It exercises: partial executions of one order, two different same-day orders for the same asset, a bond bought with accrued interest and tax-credit side-rows, dividend and coupon with withholding, stamp duty and Tobin tax, deposits, and one row of an unrecognized type.

Not covered because no real sample exists yet: sales, withdrawals, bond redemptions, foreign currency, corporate actions (see `docs/import/directa.md`).

**Never add real exports here.** Real files go in `/private/` (git-ignored).
