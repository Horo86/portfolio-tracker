# Portfolio Tracker — Domain Model

Status: V1 model, consistent with `decisions.md` (up to D-52) and `domain-rules.md`.

## Principles

- **Transactions and market data (prices, FX) are the only sources of truth.** Positions, cash balances, valuations, realized/unrealized gains and performance are **derived**, may be cached, and must always be reproducible from the sources.
- Money and quantities use exact decimals (never binary floating point). Quantities support at least 8 decimal places.
- Every table has a stable identifier (UUID) so that future features (attachments, sharing) can reference it.

## Entities

### User
Owns accounts and preferences. Fields: id, username/email (login identifier), display name, role (`INVESTOR` | `ADMINISTRATOR`), password hash, TOTP secret (optional), recovery codes (hashed), preferences (reporting currency, language `it|en`, theme), created/disabled timestamps.
An `ADMINISTRATOR` also has Investor rights on their own data (own accounts and portfolio) but no access to other users' portfolios (D-39, D-33).

### Investment Account
A broker account. Fields: id, owner user, **name**, **provider** (free text, e.g. "Directa"), **cash currency** (single, ISO 4217), optional external account code (e.g. broker account number, used to recognize import files), created timestamp.
Multi-currency brokers are modelled as several accounts (D-03).
Lifecycle (D-39): name and provider are editable; the cash currency is locked once the account has a transaction; an account can be deleted together with its transactions after confirmation, with an audit log entry. Archiving is V2.

### Asset
Fields: id, **ISIN** (preferred identifier, may be null), ticker, name, **asset type** (`ETF` | `STOCK` | `BOND`; ETC/ETN are `ETF`), **quote currency**, **quote unit** (`CURRENCY` | `MINOR_CURRENCY_UNIT` | `PERCENT_OF_NOMINAL`; bonds always use `PERCENT_OF_NOMINAL` and only bonds use it (D-67); `MINOR_CURRENCY_UNIT` means the price is in 1/100 of the quote currency, e.g. GBX for GBP, D-44), provider-specific symbols, **owner user** (null for shared assets), metadata, created timestamp.
Visibility (D-35, D-37): assets resolved through a market data provider are shared reference data; assets created manually are private to their owner. A user sees only the assets they use or added; asset search never reveals which ISINs other users hold. The same ISIN may therefore exist once as a shared asset and once per user as a private asset. Positions are per account. Ticker is not globally unique; ISIN is preferred. The name is informational and may change between data sources (never use it as a key).

### Transaction
Historical event; primary source of truth. See `domain-rules.md` §2 for the per-type field rules.

| Field | Notes |
|---|---|
| id | UUID |
| account | owner account |
| type | `BUY`, `SELL`, `DIVIDEND`, `COUPON`, `DEPOSIT`, `WITHDRAWAL`, `STOCK_SPLIT` |
| trade_date | date (operation date), required |
| trade_time | optional time, used only for ordering |
| value_date | optional settlement date (informational column, D-48) |
| asset | required for BUY/SELL/DIVIDEND/COUPON/STOCK_SPLIT |
| quantity | shares/units; **nominal value for bonds** |
| unit_price | in the asset quote unit (currency or % of nominal); optional if derivable |
| gross_amount | in account currency (price × quantity, or gross income) |
| fees | ≥ 0, in account currency |
| accrued_interest_paid | ≥ 0, BUY of bonds only |
| accrued_interest_received | ≥ 0, SELL of bonds only (D-43) |
| taxes | signed, in account currency: positive = withheld, negative = credit; on BUY only for bonds (D-42) |
| total_amount | cash amount actually debited/credited in account currency |
| split_ratio | STOCK_SPLIT only: `new:old` |
| affects_cash | boolean, default true; BUY/SELL/DIVIDEND/COUPON only (D-08) |
| external_ref | broker reference (order reference / protocol) for duplicate detection |
| import_batch | link to the import that created it (nullable) |
| notes | free text |
| metadata | JSON; e.g. original broker movement type |
| created_at / updated_at | timestamps |

### Position (derived)
Per account and asset at a given date: quantity, total cost basis, unit average cost (PMC), market value, unrealized gain, realized gain to date, income received.

### Cash Balance (derived)
Per account at a given date: sum of the cash effect of all transactions with `affects_cash = true` up to that date (including deposits, withdrawals, trade totals, income net amounts).

### Portfolio (derived)
Aggregation of all of a user's accounts, expressed in the user's reporting currency (EUR by default). Never a separate source of truth.

### Historical Price
Per asset and date: close price (in the asset quote unit), quote currency, source (`provider` | `manual`), owner user (manual prices only). Daily close only; no OHLCV in V1. Provider prices are shared; a manual price belongs to the user who entered it and, for that user only, overrides a provider price for the same date (D-35).

### FX Rate
Per currency pair and date: rate and source. **Valuation FX** (market) is distinct from **transaction FX** (applied by the broker, preserved in the transaction amounts). A later market rate never replaces the broker's.

### Import Batch
Id, user, account, source (`directa-xlsx` | `app-csv`), file name, file hash, timestamps, counts (imported / skipped as duplicates / not recognized). Used for traceability and duplicate handling. One batch belongs to one account: an app CSV with several accounts creates one batch per account (D-38).

### Audit Log
Internal append-only log: logins and failed logins, administrative actions, transaction create/edit/delete (who, when, before/after), imports, backups/restores. Browsing UI is V2.

### Provider Configuration
Market data provider settings and credentials. Credentials encrypted at rest, never returned by the API, excluded from backups.

### Attachment (V2 — not implemented)
Reserved: a file (e.g. a PDF statement) linked to a transaction, stored under `/data/attachments`.

## Relationships

```
User ─┬─ Account ── Transaction ── Asset ── Historical Price
      │                 │
      │                 └── (derives) Position, Cash Balance
      └─ Preferences     Portfolio = aggregation of Accounts
Import Batch ── Transaction
FX Rate ── used by valuation in reporting currency
```

## V1 boundaries

Excluded or deferred: account archiving, historical FX attribution, custom assets, direct crypto holdings, multi-currency accounts, transfers between accounts, short selling, order execution, automatic dividend reinvestment, current/savings accounts, real estate, liabilities, account-level expense type, mergers/spin-offs/conversions, annualized return/XIRR/TWRR, geographic allocation, attachments. See `roadmap-v2.md`.
