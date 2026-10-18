# Portfolio Tracker — Domain Model

Status: V1 model, consistent with `decisions.md` (up to D-135) and `domain-rules.md`. Implemented in M2 (migration `0002`): users, sessions, accounts, assets, transactions, prices, audit log. Import batches arrive with M3 (migration `0003`); instruments, listings, instrument prices, FX rates, job runs and provider credentials and the engine configuration with M4 (migration `0004`); 2FA fields with M6.

## Principles

- **Transactions and market data (prices, FX) are the only sources of truth.** Positions, cash balances, valuations, realized/unrealized gains and performance are **derived**, may be cached, and must always be reproducible from the sources.
- Money and quantities use exact decimals (never binary floating point). Quantities have up to 8 decimal places (D-68).
- Every table has a stable identifier (UUID) so that future features (attachments, sharing) can reference it.

## Entities

### User
Owns accounts and preferences. Fields: id, **username** (login identifier, unique case-insensitively, D-89), display name (optional), role (`INVESTOR` | `ADMINISTRATOR`), password hash (Argon2id), `must_change_password` (temporary password, D-78, D-93), password changed at, last login at, preferences (reporting currency, language `it|en`, theme `sobrio|moderno|ambra|grafite`, D-76), created/disabled timestamps. TOTP secret and hashed recovery codes are added in M6.
An `ADMINISTRATOR` also has Investor rights on their own data (own accounts and portfolio) but no access to other users' portfolios (D-39, D-33).

### Session
An opaque login session (ADR-0005): id, user, SHA-256 hash of the token (the token itself is never stored), created at, last used at, absolute expiry, user agent. Expires after 7 days without use or 30 days after login (D-88); revoked by logout, password change (other sessions) and administrator reset/disable (D-93).

### Investment Account
A broker account. Fields: id, owner user, **name**, **provider** (free text, e.g. "Directa"), **cash currency** (single, ISO 4217), optional external account code (e.g. broker account number, used to recognize import files), created timestamp.
Multi-currency brokers are modelled as several accounts (D-03).
Lifecycle (D-39, D-90): name, provider and external code are editable; the cash currency is locked while the account has a transaction; an account can be deleted together with its transactions after confirmation, with an audit log entry. Archiving is V2.

### Asset
Fields: id, **ISIN** (preferred identifier, may be null), ticker, name, **asset type** (`ETF` | `STOCK` | `BOND`; ETC/ETN are `ETF`), **quote currency**, **quote unit** (`CURRENCY` | `MINOR_CURRENCY_UNIT` | `PERCENT_OF_NOMINAL`; bonds always use `PERCENT_OF_NOMINAL` and only bonds use it (D-67); `MINOR_CURRENCY_UNIT` means the price is in 1/100 of the quote currency, e.g. GBX for GBP, D-44), provider-specific symbols (unused, D-117), **owner user**, **instrument** (nullable link, set for an asset with an ISIN once it is resolved; removed if the instrument row is ever removed, D-116), metadata, created timestamp.
Visibility (D-35, D-37, D-116): from M4 every asset is private to its owner, whether added from asset search or by hand; owner-less (shared) assets are not created in V1 (D-129). A user sees only their own assets; asset search never reveals which ISINs other users hold. At most one asset per ISIN and user (D-91). Provider prices reach an asset through its instrument; the instrument and the other users linked to it are never visible. Type, quote currency, quote unit and ISIN are locked while a transaction uses the asset; only unused assets can be deleted (D-91). Positions are per account. Ticker is not globally unique; ISIN is preferred. The name is informational and may change between data sources (never use it as a key).

### Transaction
Historical event; primary source of truth. See `domain-rules.md` §2 for the per-type field rules.

| Field | Notes |
|---|---|
| id | UUID |
| account | owner account |
| type | `BUY`, `SELL`, `DIVIDEND`, `COUPON`, `DEPOSIT`, `WITHDRAWAL`, `STOCK_SPLIT` |
| trade_date | date (operation date), required |
| trade_time | optional time, used only for ordering |
| sequence | insertion sequence, the last tie-breaker of the replay order (§4, D-45). Integer, **unique per account**; assigned by the app in increasing order when a transaction is created; on import, in the order of the file rows (chronological, see `import/directa.md` §8); **unchanged** when the transaction is edited, including a date change (D-70); a transaction moved to another account (same currency only, D-98) gets the next sequence of that account (D-85) |
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
| import_batch | link to the import that created it (`import_batch_id`, nullable; added in M3; set to null if the batch row is ever removed) |
| notes | free text |
| metadata | JSON; e.g. original broker movement type |
| created_at / updated_at | timestamps |

### Position (derived)
Per account and asset at a given date: quantity, total cost basis, unit average cost (PMC), market value, unrealized gain, realized gain to date, income received. Like every derived entity it is computed on request by replaying the transactions (D-84).

### Cash Balance (derived)
Per account at a given date: sum of the cash effect of all transactions with `affects_cash = true` up to that date (including deposits, withdrawals, trade totals, income net amounts).

### Portfolio (derived)
Aggregation of all of a user's accounts, expressed in the user's reporting currency (EUR by default). Never a separate source of truth.

### Historical Price (manual)
Per asset and date: close price (in the asset quote unit), source, owner user; the quote currency is the asset's (table `prices`, D-87). From M4 the table holds the users' **manual** closes only (`source = MANUAL`; `PROVIDER` is no longer written, D-129). Daily close only; no OHLCV in V1. A manual price belongs to the user who entered it and, for that user only, wins on its own day; otherwise the most recent dated price applies, manual or provider (D-116, `domain-rules.md` §12).

### Instrument (internal)
The shared identity behind users' assets: id, **ISIN**, **quote currency** (unique together, D-125), **price class** (`ETF_STOCK` | `BOND`, empty until known) with the engine that gave it or "inferred from the unit" (D-133), created timestamp. Never returned by the API, not even its id; no route lists instruments or the users linked to them (D-116).

### Instrument Engine Check (internal)
The last search of one engine for an instrument (one row per instrument and engine): status (`OK`: it had an accepted listing | `NOT_FOUND` | `MISMATCH` | `FAILING`), mismatch kind (currency or unit, D-133; present exactly when the status is `MISMATCH`), time of the last answered search, last error code, consecutive failure count. `NOT_FOUND` and `MISMATCH` are searched again 30 days after the last answered search, `FAILING` on the next run (D-134). Counted per engine and status for the administrator (D-135).

### Instrument Listing (internal)
A symbol an engine offers for an instrument, accepted when it was found (unique per instrument, engine and symbol; an engine may hold several, e.g. a replacement back-filled before the old one goes, D-134): instrument, provider name, symbol, exchange name and code (D-125), currency and unit as the provider reports them, how it was matched (by ISIN, or by a classifier's hint, D-136), status of its closes (`OK` | `NOT_FOUND` | `MISMATCH` | `FAILING`), mismatch detail (currency or unit, D-119; level, D-134), active flag, date it became active, last success, last error code, consecutive failure count. **At most one listing per instrument is active**: the one whose closes the linked assets read, chosen per D-133 (engine order of the instrument's price class) and D-125 (candidates ranked by preferred exchanges, then the engine's main exchanges, then other venues; never from a user's data), and replaced only as D-134 says (make before break, never stitched). A unit mismatch with a user's asset is decided per asset when closes are read (D-119), not stored on the listing.

### Instrument Price (internal)
Per listing and day: close in the major unit of the instrument's quote currency (a close in pence is stored in pounds, exact ÷100), and metadata (e.g. the provider's original unit). Each linked asset reads it in its own quote unit (×100 for `MINOR_CURRENCY_UNIT`, D-44, D-119). Shared by every asset linked to the instrument. Fetched from the last stored day (D-122); stored as received from the provider, including split adjustments (D-126). Keyed by listing and day, without a UUID: high-volume data (up to about a million rows at the reference size) that nothing else references. FX rates are keyed the same way (currency and day).

### Job Run
One market data run: kind (daily, catch-up, back-fill, manual trigger, re-resolution after a configuration change, D-134), started and finished timestamps, result, error counts by code. Read by the administrator status route; never contains ISINs, symbols or figures of users (D-121).

### Provider Credential
Provider name, nonce, ciphertext (AES-GCM, the provider name as associated data), created timestamp. Never returned by the API, never logged, excluded from backups together with the `secrets` folder holding the key file (D-21c, D-124).

### FX Rate
Per currency and date: rate in units of that currency per 1 EUR, and source (e.g. ECB through Frankfurter); other pairs are crossed through EUR (`domain-rules.md` §9). Fetched from the last stored day (D-122); a rate older than 5 days is used with a notice (D-120). **Valuation FX** (market) is distinct from **transaction FX** (applied by the broker, preserved in the transaction amounts). A later market rate never replaces the broker's.

### Import Batch
Id, user, account (deleted with the account), source (`directa-xlsx` | `app-csv`), file name, file hash (SHA-256), created timestamp, counts (imported / skipped as duplicates / not recognized / to review not selected / ambiguous / skipped by choice). Created only at confirmation: previews store nothing (D-100). Used for traceability; duplicate detection itself uses `id` and `external_ref` (D-38). One batch belongs to one account: an app CSV with several accounts creates one batch per account (D-38). Confirming writes one `import.confirmed` audit entry per batch (D-109). The file content is never stored; for Directa the holder name is never stored either.

### Audit Log
Internal append-only log: logins and failed logins, administrative actions, transaction create/edit/delete (who, when, before/after), imports, backups/restores. Browsing UI is V2. Fields: at, actor user, action (e.g. `auth.login_failed`, `user.password_reset`, `account.deleted`, `transaction.updated`), entity type and id, before/after JSON, client IP.

### Provider Configuration
Stored in the database (D-132): per engine an on/off switch; per role (`ETF_STOCK`, `BOND`, `FX`, `CLASSIFY`) an ordered list of engines; the preferred exchanges (D-125). Seeded from environment variables while nothing is stored; `PT_MARKET_DATA=off` always wins; engines added by an upgrade start disabled. What each engine can do is declared by its adapter, not stored (D-131). Credentials are Provider Credential rows. The admin API (D-135) and screens to edit all of this come in M6.

### Attachment (V2 — not implemented)
Reserved: a file (e.g. a PDF statement) linked to a transaction, stored under `/data/attachments`.

## Relationships

```
User ─┬─ Account ── Transaction ── Asset ── Historical Price (manual, per user)
      │                                 └── Instrument (internal) ─┬─ Engine Check (per engine)
      │                                                            └─ Listing ── Instrument Price
      │                 │
      │                 └── (derives) Position, Cash Balance
      └─ Preferences     Portfolio = aggregation of Accounts
Import Batch ── Transaction
FX Rate ── used by valuation in reporting currency
Job Run, Provider Credential, Provider Configuration ── market data operations (internal)
```

## V1 boundaries

Excluded or deferred: account archiving, historical FX attribution, custom assets, direct crypto holdings, multi-currency accounts, transfers between accounts, short selling, order execution, automatic dividend reinvestment, current/savings accounts, real estate, liabilities, account-level expense type, mergers/spin-offs/conversions, annualized return/XIRR/TWRR, geographic allocation, attachments. See `roadmap-v2.md`.
