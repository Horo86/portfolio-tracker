# Portfolio Tracker — Domain Rules (V1)

Status: V1 rules, consistent with `decisions.md`. Numeric examples in §16 are the acceptance reference for the calculation engine (they must become automated tests).

## 0. Conventions

- Exact decimal arithmetic only. Quantities: at least 8 decimals. Money is stored with the precision provided by the broker/user and **not** rounded to cents when derived.
- Derived divisions (cost removed on a sale, PMC) use 8 decimals with round-half-even; **remaining cost basis = previous cost basis − cost removed** (so totals never drift). Display rounds to 2 decimals (money) and up to 4 decimals (unit prices).
- Dates are calendar dates without time zone. All amounts in a transaction are in the **account currency** unless explicitly stated (unit price is in the asset's quote unit).
- "Gross/net" always refers to taxes withheld by the broker (see §6).

## 1. Cost basis and average cost (PMC)

Broker-style average cost (PMC, "prezzo medio di carico"):

- BUY: cost added = `gross_amount + fees`. Accrued interest paid and taxes on a BUY are **not** part of cost basis.
- After a BUY: `PMC = total cost basis / total quantity` over the remaining position plus the new purchase.
- SELL: cost removed = `cost basis × quantity sold / quantity held` (i.e. quantity sold × current PMC). A sale does not change the PMC of the remaining position.
- Net sale proceeds = `gross_amount − fees` (before taxes).
- Realized gain (gross) = net sale proceeds − cost removed. Partial sales realize gain only on the quantity sold.
- STOCK_SPLIT changes quantity and PMC inversely; total cost basis is unchanged (§8).

## 2. Transaction types and field rules

Common fields: account, type, `trade_date` (required), optional `trade_time`, `value_date`, `notes`, `external_ref`, `metadata`.
All amounts are in account currency. `taxes` is signed: positive = withheld by the broker, negative = tax credit.

| Type | Required | Optional | Invariant | Cash effect (if `affects_cash`) |
|---|---|---|---|---|
| `BUY` | asset, quantity > 0, `gross_amount` ≥ 0, `total_amount` | `unit_price`, `fees` ≥ 0 (default 0), `accrued_interest_paid` ≥ 0 (bonds only, default 0), `taxes` (default 0) | `total_amount = gross_amount + fees + accrued_interest_paid + taxes` | `− total_amount` |
| `SELL` | asset, quantity > 0 (≤ position), `gross_amount`, `total_amount` | `unit_price`, `fees`, `taxes` | `total_amount = gross_amount − fees − taxes` | `+ total_amount` |
| `DIVIDEND` | asset, `gross_amount` > 0, `total_amount` | `taxes` ≥ 0 (default 0), informational `quantity` | `total_amount = gross_amount − taxes` | `+ total_amount` |
| `COUPON` | asset (bond), `gross_amount` > 0, `total_amount` | `taxes` ≥ 0 (default 0) | `total_amount = gross_amount − taxes` | `+ total_amount` |
| `DEPOSIT` | `total_amount` > 0 | notes | — | `+ total_amount` (always) |
| `WITHDRAWAL` | `total_amount` > 0 | notes | — | `− total_amount` (always) |
| `STOCK_SPLIT` | asset, `split_ratio` `new:old` (> 0 each) | notes | — | none |

Notes:
- Manual entry: the UI helps by computing `gross_amount` from `unit_price × quantity` (`quantity × price / 100` for bonds) and `total_amount` from the invariant, but the user may type the amounts shown on the broker statement. If the invariant does not hold the transaction is rejected with a clear message. Importers use the broker's amounts as given and flag rows that violate the invariant.
- `unit_price` is in the asset quote unit (currency, or % of nominal for bonds). For assets quoted in a different currency from the account, `gross_amount` is the amount actually converted by the broker; the implied FX rate is informational.
- `affects_cash` exists only on BUY, SELL, DIVIDEND, COUPON (§3).
- Account-level expenses (stamp duty, Tobin tax, custody) are entered as `WITHDRAWAL` with a note (D-13).

## 3. The `affects_cash` flag and starting a portfolio

- Default `true`. When `false`, the transaction counts for quantity, cost basis, realized gain and income, but **does not change the cash balance**.
- Purpose: record holdings or history without having recorded every deposit. Typical use:
  - "Add holdings I already own": a guided flow (also multi-row table entry) that creates BUYs with `affects_cash = false`, with a start date, quantity and average cost. Cash that already exists is entered as a normal `DEPOSIT`.
  - Importing a partial history: one option on the import preview, "I did not record the deposits: do not move cash", sets the flag for all imported rows.
- Users who import a complete history (deposits included) leave the flag on and get a cash balance matching the broker.
- Flagged transactions are labelled in the transaction list. An account whose cash is only partially driven by transactions shows a notice that the cash balance may not match the broker.
- **Start date caveat (V2 metrics):** for a holding entered with the flow above, the date is the start of tracking, not the original purchase date.

## 4. Validation, ordering, editing

**No short selling.** The replayed ledger of an account must never have an asset quantity below zero at any point.

**Ordering (deterministic).** Transactions of an account are replayed by `trade_date`; within the same date: transactions with `trade_time` by time; otherwise by type priority `DEPOSIT`, `STOCK_SPLIT`, `BUY`, `DIVIDEND`/`COUPON`, `SELL`, `WITHDRAWAL`; remaining ties by insertion sequence (importers assign the sequence in chronological file order — for descending Directa files, the reverse of file order). Rationale: avoid spurious negative-quantity errors for same-day buy/sell.

**Create / edit / delete.** After the change the account ledger is replayed:
- If any asset quantity would go below zero → **rejected**; nothing is changed; the message names the first conflicting transaction.
- If cash would go below zero at some date → **accepted with a warning** (account shows the first date on which cash is negative). A BUY/SELL/… with `affects_cash = false` never contributes to this warning.
- Edit is **atomic**: validation applies to the final state only, never to intermediate steps. Correcting a wrong BUY by *editing* it is the supported path (delete + re-create may be rejected if later SELLs depend on it).
- Required capabilities to avoid deadlocks: (1) atomic edit of one transaction; (2) **bulk edit** of several selected transactions validated together; (3) **delete all transactions of an asset in an account** in one operation.
- Imports are validated as a whole batch before confirmation; errors are shown in the preview.
- Delete is a real delete plus an audit log entry.
- After every create/edit/delete, derived data (positions, cash, valuation series) is recalculated and must be consistent when the response returns.

## 5. Realized and unrealized gain

- Unrealized gain (gross, also = net) = current market value − cost basis of open positions. Unrealized % = unrealized / cost basis.
- Realized gain (gross) = Σ (net sale proceeds − cost removed). Realized % = realized gain / Σ cost removed (lifetime, per asset / account / portfolio).
- No tax is estimated on unrealized gain.

## 6. Income, taxes, gross and net

- Dividends and coupons: record `gross_amount`, `taxes`, `total_amount` (net). Quantity unchanged. Net income increases cash (if `affects_cash`). Automatic reinvestment is out of scope; reinvestment is an explicit BUY.
- **Taxes are recorded, never calculated.** Sources: `taxes` on DIVIDEND/COUPON, on SELL (capital gains tax withheld, negative = credit) and on BUY (credits on accrued interest/discount, signed).
- Income of an asset (gross) = Σ gross dividends/coupons − Σ `accrued_interest_paid` on its BUYs. (Accrued interest paid at purchase is returned by the first coupon, so it is subtracted.)
- Taxes of an asset = Σ taxes on its DIVIDEND/COUPON + SELL + BUY.
- **Gain (gross)** = unrealized + realized + income (gross, as above).
- **Gain (net)** = gain (gross) − taxes.
- Shown for every asset, account and the portfolio. Income is shown in € only (no percentage in V1). The dashboard highlights the net figure and shows gross beside it; unrealized is identical in both, with a note.

## 7. Bonds

- `quantity` is the **nominal value** (in the bond currency units); price is **% of nominal**: `gross_amount = quantity × price / 100`.
- Market value = `nominal × latest clean price / 100`. Prices are "clean" (no accrued interest): the displayed value is slightly below what a sale would credit; the difference is recovered through the coupon. The UI discloses this.
- Purchase on the secondary market: accrued interest paid goes into `accrued_interest_paid` (cash only, not cost basis). Broker rows such as withholding credits on accrued interest or on issue discount are `taxes` (negative). Their exact meaning must be confirmed per broker (see `docs/import/directa.md`).
- Maturity/redemption in V1: enter a SELL at 100% of nominal on the maturity date (D-19).
- Coupons (including inflation-linked coupons that include revaluation) are recorded as `COUPON` as paid; the revaluation is not separated in V1.

## 8. Stock splits

`STOCK_SPLIT` with ratio `new:old` (2:1 doubles quantity; 1:10 is a reverse split). Quantity × new/old; PMC × old/new; total cost basis unchanged; no cash flow; no realized gain. Recorded per account (each account is independent). Historical prices are stored as received; the engine does not retroactively adjust them (valuation uses the quantity as of each date and the price quoted on that date).

## 9. Currencies and FX

- An account has one cash currency. Assets may be quoted in another currency; the broker's actual conversion is preserved in the transaction amounts (account currency). Cost basis is in account currency.
- Valuation of a position in a foreign currency = quantity × price × market FX (ECB reference rate, last available on or before the date).
- Portfolio valuation converts account values to the user's reporting currency (default EUR) with market FX. Changing the reporting currency never alters stored data.

## 10. Account-level expenses

Not a transaction type in V1 (D-13). Entered/imported as `WITHDRAWAL` with a note (e.g. "Stamp duty"). They reduce cash and portfolio value but do **not** affect asset cost basis, gain or percentages. Known limitation, documented in the UI help. V2 introduces a typed expense and converts imported ones using stored metadata.

## 11. Cash

Cash balance at a date = Σ cash effects (§2) of transactions up to and including that date (in replay order) with `affects_cash = true`. Deposits and withdrawals change cash but not positions or cost basis. Portfolio value = Σ market value of positions + Σ cash balances.

## 12. Market data and missing data

- Prices: updated once a day after market close for all assets with an open position, and back-filled from the first transaction date for a newly added asset. History is kept locally.
- Non-trading days (weekends/holidays): carry forward the last available price (no warning).
- Stale price: if the latest price is older than 5 calendar days the asset is flagged "price not updated since <date>"; the value stays in totals.
- No price ever available: the position is valued **at cost basis**, flagged "estimated at cost"; unrealized gain is 0 until a price exists.
- Manual price: a user can enter a close price for an asset and a date (e.g. final redemption price of a delisted instrument). It overrides a provider price on the same date and is flagged as manual.
- FX: same carry-forward rule. If no rate exists for a pair, the affected value is **not** converted: it is reported explicitly as unavailable and totals are marked partial. Never invent numbers.
- Charts start at the first date for which data is complete, with a note.
- Providers are replaceable behind one interface (D-16); provider failures never block the app: they only stop new prices.

## 13. Portfolio aggregation and allocations

- Portfolio = all accounts of a user, positions plus cash, in reporting currency.
- Allocations (current): by asset (share of market value), by asset type, by quote currency (cash counts in its account currency). Geographic allocation is V2.
- Best/worst performers: ranked by unrealized % on open positions.
- Historical value series: for each day, Σ (quantity held that day × price that day × FX) + cash that day.

## 14. Performance metrics (V1)

Absolute and percentage figures only: unrealized (€, %), realized (€, %), income (€), taxes (€), gain gross (€), gain net (€), invested capital (cost basis of open positions). Annualized return, XIRR and TWRR are V2 (D-09). Each metric has a plain-language explanation in the UI.

## 15. Derived data

Positions, cash, valuation series and metrics are derived and reproducible. Caches are allowed but never authoritative; there must be a way to rebuild all derived data from transactions and market data.

## 16. Worked examples (acceptance tests)

All amounts in EUR, account currency EUR.

**E1 — Average cost.**
BUY 10 @ 100.00, fees 5.00 → gross 1000.00, cost 1005.00, PMC 100.50.
SELL 4 @ 120.00, fees 2.00 → gross 480.00, net proceeds 478.00, cost removed 402.00, **realized +76.00**; remaining 6, cost 603.00, PMC 100.50.
BUY 4 @ 110.00, fees 1.00 → cost 441.00; total 10 units, cost 1044.00, **PMC 104.40**.

**E2 — Splits.** 10 shares, PMC 100 (cost 1000). Split 2:1 → 20 shares, PMC 50, cost 1000. Then reverse 1:10 → 2 shares, PMC 500, cost 1000. No cash, no realized gain.

**E3 — Gross vs net.**
BUY 10 @ 100.00 (no fees): cost 1000.00. SELL 4 @ 120.00: gross 480.00, `taxes` 20.80 withheld, `total_amount` 459.20 → cost removed 400.00, realized gross +80.00. Remaining 6 units valued at 110.00 = 660.00 → unrealized +60.00 (+10.0% of 600.00). Dividend gross 20.00, taxes 5.20, net 14.80.

| | Gross | Taxes | Net |
|---|---|---|---|
| Unrealized | +60.00 | – | +60.00 |
| Realized (+20.0% on cost sold 400.00 gross; +14.8% net) | +80.00 | 20.80 | +59.20 |
| Income | +20.00 | 5.20 | +14.80 |
| **Total** | **+160.00** | **26.00** | **+134.00** |

**E4 — Bond bought on the secondary market (nominal 5000).**
Price 97.50% → gross 4875.00; fees 5.00; accrued interest paid 12.34; taxes −2.44 (two credits: −1.54 and −0.90). Cost basis = 4875.00 + 5.00 = **4880.00**. `total_amount` = 4875.00 + 5.00 + 12.34 − 2.44 = **4889.90** (cash −4889.90).
Coupon: gross 50.00, taxes 6.25, net 43.75 (cash +43.75).
Income of the asset (gross) = 50.00 − 12.34 = **37.66**; taxes = 6.25 − 2.44 = **3.81**; net income = **33.85**. Check on cash: +43.75 − (12.34 − 2.44) = 33.85.
Valuation at price 98.20 → 5000 × 98.20 / 100 = 4910.00 → unrealized **+30.00** (+0.61%).

**E5 — Existing holding.** 50 shares at average cost 8.00 entered with `affects_cash = false`: gross 400.00, cash unchanged, cost basis 400.00, PMC 8.00. A later BUY 10 @ 9.00 with cash adds 90.00: 60 units, cost basis 490.00, PMC 8.1667.

**E6 — Editing.** Ledger: BUY 10 (Jan), SELL 10 (Feb). Delete the BUY → rejected (Feb SELL would need 10 units). Edit the BUY quantity to 12 → accepted (2 left). Edit it to 8 → rejected. Move the BUY date after the SELL → rejected. Delete the SELL → accepted. "Delete all transactions of the asset" → accepted.

**E7 — Same-day ordering.** BUY 10 and SELL 10 on the same date, no time: processed as BUY then SELL → valid.

## 17. Open items (to define when the related feature is built)

- Import of SELL, withdrawals, redemptions, foreign-currency trades and corporate actions from real broker samples (see `docs/import/directa.md`).
- Exact meaning of broker side-rows on bond purchases (withholding credit on accrued interest, discount credit) — confirm with the broker.
- Whether 2FA secrets and password hashes are included in an unencrypted backup (current assumption: yes, with a strong recommendation to encrypt).
- Calendar handling for markets with different holidays beyond carry-forward.
