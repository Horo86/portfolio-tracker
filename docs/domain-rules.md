# Portfolio Tracker — Domain Rules (V1)

Status: V1 rules, consistent with `decisions.md` (up to D-73). Numeric examples in §16 are the acceptance reference for the calculation engine (they must become automated tests).

## 0. Conventions

- Exact decimal arithmetic only. Quantities: up to 8 decimals; a quantity with more significant decimals is rejected (D-68). Money is stored with the precision provided by the broker/user and **not** rounded to cents when derived.
- **Rounding mode (the only one in the engine):** every derived division (cost removed on a sale, PMC, unit price derived from amounts, currency conversion, quantity after a split, percentages, a position value) is rounded to **8 decimals with round-half-even**, once, on the result (D-56, D-71). Sums and differences are exact. **Remaining cost basis = previous cost basis − cost removed**, so totals never drift.
- Percentages are computed as ratios (`0.10000000` = +10%); with a zero base they are "not available", never 0 (D-57).
- Rounded display values are **never** fed back into calculations. Display rounds to 2 decimals (money, percentages) and up to 4 decimals (unit prices, PMC).
- Dates are calendar dates without time zone. All amounts in a transaction are in the **account currency** unless explicitly stated (unit price is in the asset's quote unit, see §2).
- "Gross/net" always refers to taxes withheld by the broker (see §6).

## 1. Cost basis and average cost (PMC)

Broker-style average cost (PMC, "prezzo medio di carico"):

- BUY: cost added = `gross_amount + fees`. Accrued interest paid and taxes on a BUY are **not** part of cost basis.
- After a BUY: `PMC = total cost basis / total quantity` over the remaining position plus the new purchase.
- SELL: **cost removed = cost basis × quantity sold / quantity held** (D-40). When the whole position is sold, cost removed = the remaining cost basis exactly (D-63). This is the only formula; the PMC is a display value and is never multiplied back. A sale does not change the PMC of the remaining position (up to the last decimal of §0).
- Net sale proceeds = `gross_amount − fees` (before taxes; accrued interest received is income, §6).
- Realized gain (gross) = net sale proceeds − cost removed. Partial sales realize gain only on the quantity sold.
- STOCK_SPLIT changes quantity and PMC inversely; total cost basis is unchanged (§8).

## 2. Transaction types and field rules

Common fields: account, type, `trade_date` (required), optional `trade_time`, optional `value_date` (settlement date, informational column, D-48), `notes`, `external_ref`, `metadata`.
All amounts are in account currency. `taxes` is signed: positive = withheld by the broker, negative = tax credit.

| Type | Required | Optional | Invariant | Cash effect (if `affects_cash`) |
|---|---|---|---|---|
| `BUY` | asset, quantity > 0, `gross_amount` ≥ 0, `total_amount` | `unit_price` ≥ 0, `fees` ≥ 0 (default 0), `accrued_interest_paid` ≥ 0 (bonds only, default 0), `taxes` (bonds only, signed, default 0) | `total_amount = gross_amount + fees + accrued_interest_paid + taxes` | `− total_amount` |
| `SELL` | asset, quantity > 0 (≤ position), `gross_amount` ≥ 0, `total_amount` | `unit_price` ≥ 0, `fees` ≥ 0, `accrued_interest_received` ≥ 0 (bonds only, default 0), `taxes` (signed) | `total_amount = gross_amount + accrued_interest_received − fees − taxes` | `+ total_amount` |
| `DIVIDEND` | asset, `gross_amount` > 0, `total_amount` | `taxes` ≥ 0 (default 0), informational `quantity` > 0 | `total_amount = gross_amount − taxes` | `+ total_amount` |
| `COUPON` | asset (bond), `gross_amount` > 0, `total_amount` | `taxes` ≥ 0 (default 0) | `total_amount = gross_amount − taxes` | `+ total_amount` |
| `DEPOSIT` | `total_amount` > 0 | notes | — | `+ total_amount` (always) |
| `WITHDRAWAL` | `total_amount` > 0 | notes | — | `− total_amount` (always) |
| `STOCK_SPLIT` | asset, `split_ratio` `new:old` (> 0 each) | notes | — | none |

Notes:
- Manual entry: the UI helps by computing `gross_amount` from the price (see quote units below) and `total_amount` from the invariant, but the user may type the amounts shown on the broker statement. If the invariant does not hold the transaction is rejected with a clear message. The check is an exact decimal comparison with no tolerance (D-60). Importers use the broker's amounts as given and flag rows that violate the invariant.
- **Quote units** (asset field, D-44):
  - `CURRENCY`: price per unit in the quote currency; `gross_amount = quantity × price`.
  - `MINOR_CURRENCY_UNIT`: price in the minor unit of the quote currency (e.g. GBX = pence of GBP); the price in the quote currency is `price / 100`, so `gross_amount = quantity × price / 100`. The asset's quote currency is the major currency (GBP).
  - `PERCENT_OF_NOMINAL` (bonds): `gross_amount = quantity × price / 100`. Bonds always use this unit and no other asset type may use it (D-67).
- For assets quoted in a different currency from the account, `gross_amount` is the amount actually converted by the broker; the implied FX rate is informational.
- BUY `taxes` exist only for bonds (credits on accrued interest or issue discount, D-42). `accrued_interest_paid` and `accrued_interest_received` exist only for bonds.
- `affects_cash` exists only on BUY, SELL, DIVIDEND, COUPON (§3).
- The table is a closed list: a field not listed for a type is rejected (D-62), even when it is given as 0; an optional amount that is not given counts as 0 (D-67). The same holds for the bond-only fields on other asset types.
- Account-level expenses (stamp duty, Tobin tax, custody) are entered as `WITHDRAWAL` with a note (D-13).

## 3. The `affects_cash` flag and starting a portfolio

- Default `true`. When `false`, the transaction counts for quantity, cost basis, realized gain and income, but **does not change the cash balance**.
- Purpose: record holdings or history without having recorded every deposit. Typical use:
  - "Add holdings I already own": a guided flow (also multi-row table entry) that creates BUYs with `affects_cash = false`, with a start date, quantity and average cost. Cash that already exists is entered as a normal `DEPOSIT`.
  - Importing a partial history: one option on the import preview, "I did not record the deposits: do not move cash", sets the flag for all imported BUY/SELL/DIVIDEND/COUPON rows (importer-specific handling of deposits and expenses: see `docs/import/directa.md`).
- Users who import a complete history (deposits included) leave the flag on and get a cash balance matching the broker.
- Flagged transactions are labelled in the transaction list. An account whose cash is only partially driven by transactions shows a notice that the cash balance may not match the broker.
- **Start date caveat (V2 metrics):** for a holding entered with the flow above, the date is the start of tracking, not the original purchase date.

### 3.1 Moving a holding between accounts (D-47)

Transfers are not a transaction type in V1. To move a quantity from account A to account B:
- on A: a `SELL` with `affects_cash = false`, `fees = 0`, `taxes = 0`, and `gross_amount` = the exact cost removed for that quantity (§1, computed by the app, never quantity × displayed PMC) → realized gain exactly 0;
- on B: a `BUY` with `affects_cash = false` and `gross_amount` equal to the same cost (no fees) → B starts with the original average cost.

Known limitation: on A the transfer adds its cost to the realized % base (§5) with zero gain. Example E10.

## 4. Validation, ordering, editing

**No short selling.** The replayed ledger of an account must never have an asset quantity below zero at any point.

**Ordering (deterministic, D-45).** Transactions of an account are replayed by `trade_date`. Within the same date:
1. transactions **without** `trade_time` first, by type priority `DEPOSIT`, `STOCK_SPLIT`, `BUY`, `DIVIDEND`/`COUPON`, `SELL`, `WITHDRAWAL`;
2. then transactions **with** `trade_time`, by time;
3. remaining ties by insertion sequence (unique per account, assigned at creation and never changed by an edit, D-70; importers assign the sequence in chronological file order — for descending Directa files, the reverse of file order; see `docs/import/directa.md` §8 for merged rows).

Rationale: avoid spurious negative-quantity errors for same-day buy/sell.

**Create / edit / delete.** After the change the account ledger is replayed:
- If any asset quantity would go below zero, or a split would round a held quantity to zero (D-69) → **rejected**; nothing is changed; the message names the first conflicting transaction.
- If cash would go below zero at some date → **accepted with a warning** (account shows the first date whose end-of-day cash is negative; a dip recovered within the same day does not warn, D-61). A BUY/SELL/… with `affects_cash = false` never contributes to this warning.
- A DIVIDEND or COUPON on an asset never held in that account up to its date → **accepted with a warning** (D-72).
- A transaction dated after today (UTC) → **accepted with a warning** (likely a mistyped date, D-96). It still counts: current figures are valued on the later of today and the latest transaction date (D-95).
- Edit is **atomic**: validation applies to the final state only, never to intermediate steps. Correcting a wrong BUY by *editing* it is the supported path (delete + re-create may be rejected if later SELLs depend on it).
- Required capabilities to avoid deadlocks: (1) atomic edit of one transaction; (2) **bulk edit** of several selected transactions validated together; (3) **delete all transactions of an asset in an account** in one operation.
- Imports are validated as a whole batch before confirmation; errors are shown in the preview.
- Delete is a real delete plus an audit log entry. Deleting an account deletes its transactions, after confirmation, with an audit log entry (D-39).
- After every create/edit/delete, derived data (positions, cash, valuation series) is recalculated and must be consistent when the response returns.

## 5. Realized and unrealized gain

- Unrealized gain (gross, also = net) = current market value − cost basis of open positions. **Unrealized %** = unrealized / cost basis of open positions.
- Realized gain (gross) = Σ (net sale proceeds − cost removed). Realized gain (net) = realized gross − Σ SELL `taxes`.
- **Realized % gross** = realized gross / Σ cost removed; **realized % net** = realized net / Σ cost removed (lifetime, per asset / account / portfolio; D-41).
- No tax is estimated on unrealized gain.

## 6. Income, taxes, gross and net

- Dividends and coupons: record `gross_amount`, `taxes`, `total_amount` (net). Quantity unchanged. Net income increases cash (if `affects_cash`). Automatic reinvestment is out of scope; reinvestment is an explicit BUY.
- A DIVIDEND or COUPON on a closed position (quantity 0 on that date) is allowed (D-52). On an asset never held in that account up to that date (including income dated before the first BUY) it is allowed with a warning, because the history may be incomplete (D-72).
- **Taxes are recorded, never calculated.**
- **Income of an asset (gross)** = Σ gross dividends/coupons + Σ `accrued_interest_received` on its SELLs − Σ `accrued_interest_paid` on its BUYs. (Accrued interest paid at purchase is returned by the next coupon or by the sale, so it is subtracted.)
- **Where taxes land (D-42):**

| Source | Counts as |
|---|---|
| `taxes` on DIVIDEND / COUPON | income taxes |
| `taxes` on BUY (bonds only: credits on accrued interest / issue discount, usually negative) | income taxes |
| `taxes` on SELL (capital gains tax withheld, negative = credit) | realized taxes |

- **Known simplification:** a withholding on accrued interest received on a bond SELL is part of the SELL `taxes`, so it appears under realized taxes, not income taxes. The net total is unaffected. (A separate field is listed in `roadmap-v2.md`.)
- Taxes of an asset = income taxes + realized taxes.
- **Gain (gross)** = unrealized + realized gross + income gross. **Gain (net)** = gain (gross) − taxes.
- Shown for every asset, account and the portfolio. **Total gain and income are shown in € only** (gross and net); percentages exist only for unrealized and realized (§5). The dashboard highlights the net figure and shows gross beside it; unrealized is identical in both, with a note.

## 7. Bonds

- `quantity` is the **nominal value** (in the bond currency units); price is **% of nominal**: `gross_amount = quantity × price / 100`. The PMC of a bond is displayed as % of nominal (cost basis / nominal × 100; the engine returns it per unit, the API multiplies by 100, D-66).
- Market value = `nominal × latest clean price / 100`. Prices are "clean" (no accrued interest): the displayed value is slightly below what a sale would credit; the difference is recovered through the coupon or the accrued interest received on sale. The UI discloses this.
- Purchase on the secondary market: accrued interest paid goes into `accrued_interest_paid` (cash only, not cost basis). Broker rows such as withholding credits on accrued interest or on issue discount are BUY `taxes` (negative). Their exact meaning must be confirmed per broker (see `docs/import/directa.md`).
- Sale on the secondary market: accrued interest credited goes into `accrued_interest_received` (D-43): gross income, part of the cash movement, not part of proceeds, cost basis or realized gain. Example E9.
- Maturity/redemption in V1: enter a SELL at 100% of nominal on the maturity date (D-19).
- Coupons (including inflation-linked coupons that include revaluation) are recorded as `COUPON` as paid; the revaluation is not separated in V1.

## 8. Stock splits

`STOCK_SPLIT` with ratio `new:old` (2:1 doubles quantity; 1:10 is a reverse split). Quantity × new/old (rounded as in §0, D-56); a split that would round a non-zero quantity to 0 is rejected like a negative quantity (D-69); PMC × old/new; total cost basis unchanged; no cash flow; no realized gain. Recorded per account (each account is independent). Historical prices are stored as received; the engine does not retroactively adjust them (valuation uses the quantity as of each date and the price quoted on that date).

Fractions: if the broker pays cash in lieu of a fractional quantity, enter the split for the full ratio and then a `SELL` of the fractional quantity for the cash received (D-52).

## 9. Currencies and FX

- An account has one cash currency. Assets may be quoted in another currency; the broker's actual conversion is preserved in the transaction amounts (account currency). Cost basis is in account currency.
- Valuation of a position in a foreign currency = quantity × price (in the quote currency, §2 quote units) × market FX (ECB reference rate, last available on or before the date). ECB rates are quoted against EUR; a pair between two non-EUR currencies is derived through EUR. The converted amount is rounded once (§0); intermediate cross rates are not rounded (D-71). A position value that needs no conversion is rounded once in the same way (D-71).
- **Per-account figures are always in the account currency** (D-36): cost basis, invested capital, unrealized, realized, income, taxes, cash, value.
- **Portfolio in the reporting currency** (default EUR): the already-computed account figures are converted
  - for current figures (value, invested capital, gains, income, taxes, cash) at the **latest** available rate;
  - for the historical value series at **each day's** rate.
- No historical FX attribution in V1: e.g. a realized gain made years ago is converted at today's rate. The limitation is documented in the UI help. Changing the reporting currency never alters stored data. Example E12.

## 10. Account-level expenses

Not a transaction type in V1 (D-13). Entered/imported as `WITHDRAWAL` with a note (e.g. "Stamp duty"). They reduce cash and portfolio value but do **not** affect asset cost basis, gain or percentages. Known limitation, documented in the UI help. V2 introduces a typed expense and converts imported ones using stored metadata.

## 11. Cash

Cash balance at a date = Σ cash effects (§2) of transactions up to and including that date (in replay order) with `affects_cash = true`. Deposits and withdrawals change cash but not positions or cost basis; accrued interest paid/received moves cash as part of the trade's `total_amount`. Portfolio value = Σ market value of positions + Σ cash balances.

## 12. Market data and missing data

- Prices: updated once a day after market close (D-121). Every fetch runs from the day after the last stored close of that series up to today, for every series still needed (not only open positions), so gaps left by provider failures or a position reopened after months are filled on the next run; a new series starts from the earliest date the provider offers, capped at 20 years before the fetch, whichever user's data made it needed (D-122). History is kept locally.
- **Price visibility (D-35, D-116):** every asset belongs to one user. An asset with an ISIN is linked to an internal instrument (ISIN + quote currency, D-125) whose provider closes are shared by every linked asset; an asset without an ISIN is priced by manual prices only. A **manual price** belongs to the user who entered it and affects only that user's valuations.
- **Price of an asset on a day (D-116):** the user's manual price on that day, if any; otherwise the most recent dated price on or before that day, whatever its source (provider close or the user's manual price). An old manual price never hides newer provider closes.
  - *Example (invented):* manual 10.00 on 3 March, provider closes 9.80 on 2 March and 10.40 on 5 March. Value on 3 and 4 March: 10.00 (manual); on 5 March: 10.40 (provider, newer).
- **Unit and currency of provider data (D-119):** a provider listing is used only when compatible with the asset. Pence ↔ pounds (GBp/GBX ↔ GBP) is rescaled exactly by 100, and the original unit is kept with the stored close; any other difference (currency, or a bond not quoted in % of nominal) is refused: that asset reads no provider closes (other users' assets on the same instrument are checked on their own), and it shows "price source mismatch" with what differs, until the provider data becomes compatible again.
- Non-trading days (weekends/holidays): carry forward the last available price (no warning).
- Stale price: if the latest price is older than 5 calendar days the asset is flagged "price not updated since <date>"; the value stays in totals.
- **No price on or before a date (D-46):** on any day for which an asset has no price on or before that day (never priced, or before its first price), the position is valued **at that day's cost basis**, flagged "estimated at cost"; unrealized gain is 0 for those days. This holds both for the current value and for the historical series, from the first transaction date.
- Manual price: a user can enter a close price for an asset and a date (e.g. final redemption price of a delisted instrument). For that user it overrides a provider price on the same date and is flagged as manual.
- FX: same carry-forward rule, and the same 5-day stale rule (D-120): a rate older than 5 calendar days is still used, but every figure converted with it carries a notice naming the currency pair and the date of the last rate (in the historical series only the stale part). FX rates are fetched like prices: from the last stored day, and a new currency from the earliest date offered, capped at 20 years (D-122). If no rate exists for a pair, the affected value is **not** converted: it is reported explicitly as unavailable and totals (value, unrealized, gain) sum the available positions and are marked partial, with a visible label (D-64, D-73). In the portfolio, an account whose currency has no rate to the reporting currency is left out entirely and every portfolio figure is marked partial (D-73). Never invent numbers.
- Charts are never blocked by missing prices. **Only missing FX** makes a chart partial: it then starts at the first date for which FX is available, with a note.
- Providers are replaceable behind one interface (D-16, D-117); provider failures never block the app: they only stop new prices, and the last stored prices stay in use with the stale notice.
- Known limitation (D-126): provider closes may be adjusted for later splits; values before a split are then not reliable, so the value-history chart waits until this is resolved.

## 13. Portfolio aggregation and allocations

- Portfolio = all accounts of a user, positions plus cash, in reporting currency (§9).
- Allocations (current): by asset (share of market value), by asset type, by quote currency (cash counts in its account currency). Geographic allocation is V2.
- Best/worst performers: ranked by unrealized % on open positions.
- Historical value series: for each day, Σ (value of each position that day × FX that day) + cash that day, where the value of a position is quantity × price that day (§2 quote units), or its cost basis that day when there is no price on or before that day (§12).

## 14. Performance metrics (V1)

Absolute and percentage figures only: unrealized (€, %), realized gross and net (€, %), income (€), taxes (€), gain gross (€), gain net (€), invested capital (cost basis of open positions). Annualized return, XIRR and TWRR are V2 (D-09). Each metric has a plain-language explanation in the UI.

## 15. Derived data

Positions, cash, valuation series and metrics are derived and reproducible. Caches are allowed but never authoritative; there must be a way to rebuild all derived data from transactions and market data.

## 16. Worked examples (acceptance tests)

All amounts in EUR, account currency EUR, unless stated. Values are exact; where a figure has more than 2 decimals the engine value is given and the display value follows in brackets.

**E1 — Average cost.**
BUY 10 @ 100.00, fees 5.00 → gross 1000.00, cost 1005.00, PMC 100.50.
SELL 4 @ 120.00, fees 2.00 → gross 480.00, net proceeds 478.00, cost removed = 1005.00 × 4 / 10 = 402.00, **realized +76.00**; remaining 6, cost 1005.00 − 402.00 = 603.00, PMC 100.50.
BUY 4 @ 110.00, fees 1.00 → cost 441.00; total 10 units, cost 1044.00, **PMC 104.40**.

**E2 — Splits.** 10 shares, PMC 100 (cost 1000). Split 2:1 → 20 shares, PMC 50, cost 1000. Then reverse 1:10 → 2 shares, PMC 500, cost 1000. No cash, no realized gain.

**E3 — Gross vs net.**
BUY 10 @ 100.00 (no fees): cost 1000.00. SELL 4 @ 120.00: gross 480.00, `taxes` 20.80 withheld (realized taxes), `total_amount` 459.20 → cost removed 1000.00 × 4 / 10 = 400.00, realized gross +80.00, realized net +59.20. Remaining 6 units valued at 110.00 = 660.00 → unrealized +60.00 (+10.00% of 600.00). Dividend gross 20.00, taxes 5.20 (income taxes), net 14.80.

| | Gross | Taxes | Net | % |
|---|---|---|---|---|
| Unrealized | +60.00 | – | +60.00 | +10.00% (on open cost 600.00) |
| Realized | +80.00 | 20.80 | +59.20 | gross +20.00%, net +14.80% (on cost removed 400.00) |
| Income | +20.00 | 5.20 | +14.80 | – (€ only) |
| **Total gain** | **+160.00** | **26.00** | **+134.00** | – (€ only) |

**E4 — Bond bought on the secondary market (nominal 5000).**
Price 97.50% → gross 4875.00; fees 5.00; accrued interest paid 12.34; taxes −2.44 (two credits: −1.54 and −0.90; BUY taxes → income taxes). Cost basis = 4875.00 + 5.00 = **4880.00** (PMC 97.60% of nominal). `total_amount` = 4875.00 + 5.00 + 12.34 − 2.44 = **4889.90** (cash −4889.90).
Coupon: gross 50.00, taxes 6.25, net 43.75 (cash +43.75).
Income of the asset (gross) = 50.00 − 12.34 = **37.66**; income taxes = 6.25 − 2.44 = **3.81**; net income = **33.85**. Check on cash: +43.75 − (12.34 − 2.44) = 33.85.
Valuation at price 98.20 → 5000 × 98.20 / 100 = 4910.00 → unrealized **+30.00** (+0.61%).

**E5 — Existing holding.** 50 shares at average cost 8.00 entered with `affects_cash = false`: gross 400.00, cash unchanged, cost basis 400.00, PMC 8.00. A later BUY 10 @ 9.00 with cash adds 90.00 (cash −90.00): 60 units, cost basis 490.00, PMC 8.16666667 (8.1667).

**E6 — Editing.** Ledger: BUY 10 (Jan), SELL 10 (Feb). Delete the BUY → rejected (Feb SELL would need 10 units). Edit the BUY quantity to 12 → accepted (2 left). Edit it to 8 → rejected. Move the BUY date after the SELL → rejected. Delete the SELL → accepted. "Delete all transactions of the asset" → accepted.

**E7 — Same-day ordering.** BUY 10 and SELL 10 on the same date, no time: processed as BUY then SELL → valid. Same date, SELL 10 without time and BUY 10 at 10:00: the untimed SELL is replayed first → rejected (quantity −10); giving the SELL a later time (e.g. 11:00) makes it valid.

**E8 — Rounding without drift.** BUY 3 units, gross 100.00, no fees: cost 100.00, PMC 33.33333333 (33.3333).
SELL 1 @ 40.00 → cost removed = 100.00 × 1 / 3 = **33.33333333** (rounded as in §0); realized +6.66666667 (6.67); remaining cost 100.00 − 33.33333333 = **66.66666667**, 2 units, PMC 33.33333334 (33.3333).
SELL 2 @ 40.00 → cost removed = 66.66666667 × 2 / 2 = **66.66666667**; realized +13.33333333 (13.33). Total realized **+20.00000000**, total cost removed **100.00000000**, remaining cost **0**.

**E9 — Bond sold on the secondary market (continues E4, after the coupon).**
SELL nominal 2000 @ 99.00% → gross 1980.00; fees 3.00; `accrued_interest_received` 8.00; `taxes` 3.13 withheld. `total_amount` = 1980.00 + 8.00 − 3.00 − 3.13 = **1981.87** (cash +1981.87).
Cost removed = 4880.00 × 2000 / 5000 = **1952.00**; net proceeds 1977.00; realized gross **+25.00** (+1.28%); realized taxes 3.13; realized net **+21.87** (+1.12%). Remaining nominal 3000, cost **2928.00**, PMC 97.60%.
Income of the asset (gross) = 50.00 + 8.00 − 12.34 = **45.66**; income taxes 3.81 (unchanged); net income **41.85**. (If part of the 3.13 is withholding on the 8.00 of accrued interest, it still counts as realized taxes: §6 simplification.)
Cash of the asset since E4: −4889.90 + 43.75 + 1981.87 = **−2864.28**.

**E10 — Moving a holding between accounts.** Account A: BUY 10 @ 100.00, fees 5.00 → cost 1005.00. Move 4 units to account B:
A: SELL 4, `affects_cash = false`, gross = 1005.00 × 4 / 10 = **402.00**, fees 0, taxes 0 → cost removed 402.00, **realized 0.00** (realized % 0.00% on base 402.00); remaining 6, cost 603.00, PMC 100.50; cash unchanged (−1005.00 from the original BUY).
B: BUY 4, `affects_cash = false`, gross 402.00, fees 0 → cost 402.00, **PMC 100.50**; cash unchanged (0.00).

**E11 — Quote in pence (GBX).** Account EUR; asset quoted in GBP with quote unit `MINOR_CURRENCY_UNIT`. BUY 100 @ 1200 GBX = 100 × 1200 / 100 = 1200.00 GBP; the broker debited gross 1500.00 EUR (implied rate EUR/GBP 0.80, informational), fees 5.00 → cost **1505.00**.
Valuation: close 1250 GBX → 12.50 GBP; ECB rate EUR/GBP 0.80 (1 GBP = 1.25 EUR) → 100 × 12.50 / 0.80 = **1562.50 EUR**; unrealized **+57.50** (+3.82%). Without the minor-unit rule the value would wrongly be 156250.00.

**E12 — Reporting currency (EUR) with a USD account.** USD account figures (account currency): cost basis 1000.00, securities 1200.00, cash 100.00, realized +50.00. Latest ECB rate EUR/USD 1.25.
Converted at the latest rate: invested capital **800.00**, securities 960.00, cash 80.00, unrealized (200.00 USD) **+160.00**, realized **+40.00** (converted at today's rate, not at the rate of the sale day: no historical attribution). With a EUR account worth 2000.00, portfolio value = 2000.00 + (1300.00 / 1.25) = **3040.00 EUR**.
History: on a past day the USD account was worth 1140.00 USD and the rate was 1.20 → **950.00 EUR**; with the EUR account at 1900.00 that day, the series point is **2850.00 EUR**.

## 17. Open items (to define when the related feature is built)

- Import of SELL, withdrawals, redemptions, foreign-currency trades and corporate actions from real broker samples (see `docs/import/directa.md`).
- Exact meaning of broker side-rows on bond purchases (withholding credit on accrued interest, discount credit) — confirm with the broker. Until then a Directa BUY with `Rit.credito disaggio` is imported only after the user selects it ("to review", D-99).
- Whether 2FA secrets and password hashes are included in an unencrypted backup (current assumption: yes, with a strong recommendation to encrypt, D-33).
- Calendar handling for markets with different holidays beyond carry-forward.
