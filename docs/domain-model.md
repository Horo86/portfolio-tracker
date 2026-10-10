# Portfolio Tracker — Domain Model

Status: V1 model, consistent with `decisions.md` and `domain-rules.md`.

## Principles

- **Transactions and market data (prices, FX) are the only sources of truth.** Positions, cash balances, valuations, realized/unrealized gains and performance are **derived**, may be cached, and must always be reproducible from the sources.
- Money and quantities use exact decimals (never binary floating point). Quantities support at least 8 decimal places.
- Every table has a stable identifier (UUID) so that future features (attachments, sharing) can reference it.

## Entities

### User
Owns accounts and preferences. Fields: id, username/email (login identifier), display name, role (`INVESTOR` | `ADMINISTRATOR`), password hash, TOTP secret (optional), recovery codes (hashed), preferences (reporting currency, language `it|en`, theme), created/disabled timestamps.

### Investment Account
A broker account. Fields: id, owner user, **name**, **provider** (free text, e.g. "Directa"), **cash currency** (single, ISO 4217), optional external account code (e.g. broker account number, used to recognize import files), created timestamp.
Multi-currency brokers are modelled as several accounts (D-03).

### Asset
Fields: id, **ISIN** (preferred identifier, may be null), ticker, name, **asset type** (`ETF` | `STOCK` | `BOND`; ETC/ETN are `ETF`), **quote currency**, **quote unit** (`CURRENCY` | `PERCENT_OF_NOMINAL`; bonds use `PERCENT_OF_NOMINAL`), provider-specific symbols, metadata, created timestamp.
Assets are shared across users (reference data); positions are per account. Ticker is not globally unique; ISIN is preferred. The name is informational and may change between data sources (never use it as a key).

### Transaction
Historical event; primary source of truth. See `domain-rules.md` §2 for the per-type field rules.

| Field | Notes |
|---|---|
| id | UUID |
| account | owner account |
| type | `BUY`, `SELL`, `DIVIDEND`, `COUPON`, `DEPOSIT`, `WITHDRAWAL`, `STOCK_SPLIT` |
| trade_date | date (operation date), required |
| trade_time | optional time, used only for ordering |
| value_date | optional settlement date (informational) |
| asset | required for BUY/SELL/DIVIDEND/COUPON/STOCK_SPLIT |
| quantity | shares/units; **nominal value for bonds** |
| unit_price | in the asset quote unit (currency or % of nominal); optional if derivable |
| gross_amount | in account currency (price × quantity, or gross income) |
| fees | ≥ 0, in account currency |
| accrued_interest_paid | ≥ 0, BUY of bonds only |
| taxes | signed, in account currency: positive = withheld, negative = credit |
| total_amount | cash amount actually debited/credited in account currency |
| split_ratio | STOCK_SPLIT only: `new:old` |
| affects_cash | boolean, default true; BUY/SELL/DIVIDEND/COUPON only (D-08) |
| external_ref | broker reference (order reference / protocol) for duplicate detection |
| import_batch | link to the import that created it (nullable) |
| notes | free text |
| metadata | JSON; e.g. original broker movement type, value date |
| created_at / updated_at | timestamps |

### Position (derived)
Per account and asset at a given date: quantity, total cost basis, unit average cost (PMC), market value, unrealized gain, realized gain to date, income received.

### Cash Balance (derived)
Per account at a given date: sum of the cash effect of all transactions with `affects_cash = true` up to that date (including deposits, withdrawals, trade totals, income net amounts).

### Portfolio (derived)
Aggregation of all of a user's accounts, expressed in the user's reporting currency (EUR by default). Never a separate source of truth.

### Historical Price
Per asset and date: close price, quote currency, source (`provider` | `manual`). Daily close only; no OHLCV in V1. A manual price overrides a provider price for the same date.

### FX Rate
Per currency pair and date: rate and source. **Valuation FX** (market) is distinct from **transaction FX** (applied by the broker, preserved in the transaction amounts). A later market rate never replaces the broker's.

### Import Batch
Id, user, account, source (`directa-xlsx` | `app-csv`), file name, file hash, timestamps, counts (imported / skipped as duplicates / not recognized). Used for traceability and duplicate handling.

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

Excluded or deferred: custom assets, direct crypto holdings, multi-currency accounts, transfers between accounts, short selling, order execution, automatic dividend reinvestment, current/savings accounts, real estate, liabilities, account-level expense type, mergers/spin-offs/conversions, annualized return/XIRR/TWRR, geographic allocation, attachments. See `roadmap-v2.md`.
