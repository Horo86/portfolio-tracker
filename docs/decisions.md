# Decision Log

All decisions below were taken during V1 requirements definition (October 2026) and are **accepted**.
Other documents are the detailed source; this log explains *what was decided and why*.
To change a decision, add a new row that supersedes the old one (do not delete history).

## Scope

| ID | Decision | Rationale |
|---|---|---|
| D-01 | **Custom assets are out of V1.** | Need manual pricing, irregular price series and a dedicated UI. Listed instruments cover the target use case. |
| D-02 | **Cryptocurrencies held directly are out of V1.** Listed crypto ETC/ETN products are normal instruments (type `ETF`). | Different data sources and logic (wallets, crypto-to-crypto swaps). |
| D-03 | **One cash currency per account.** A broker with several cash currencies is modelled as several accounts. | Keeps cash, cost basis and valuation simple. |
| D-04 | **Transfers between accounts are out of V1.** To move holdings, enter a BUY on the new account with `affects_cash = false` and the original average cost. | See D-08. |
| D-05 | **Corporate actions in V1 = stock split and reverse split only** (`STOCK_SPLIT`). The generic `CORPORATE_ACTION` type is removed. Mergers, spin-offs, conversions are V2. Until then: SELL + BUY; importers flag them as unsupported. | Only splits have fully defined rules. |
| D-09 | **Performance metrics in V1: absolute return (€) and percentage return only.** Annualized return, XIRR and TWRR move to V2 (introduced together). | Fewer edge cases, results verifiable by hand, avoids misleading numbers on partial histories. |
| D-31 | **Geographic allocation is V2.** Best/worst performers are ranked by unrealized % on open positions. | Free providers rarely provide ETF composition data. |
| D-13 | **Account-level expenses (stamp duty, Tobin tax, custody fees) have no dedicated type in V1.** They are entered/imported as `WITHDRAWAL` with a note; the importer keeps the broker's original type in metadata so V2 can convert them automatically. | Keeps cash aligned with the broker without a new type. They do not affect returns in V1 (known limitation). |
| D-28 | **PDF statements ("contabili") attached to transactions are V2.** V1 only reserves `/data/attachments` and makes the backup an archive with a manifest. | Cheap to prepare now, expensive to retrofit. |

## Transactions and editing

| ID | Decision | Rationale |
|---|---|---|
| D-06 | **Editing/deleting a transaction is accepted if the replayed ledger never has negative quantity.** Otherwise it is rejected with the first conflicting transaction. Negative cash is only a warning. Derived data is recalculated after every insert/edit/delete. | Avoid blocking users with incomplete histories while keeping positions valid. |
| D-06b | **No deadlocks.** The product must offer: atomic edit of a transaction (validated on the final state), bulk edit of several transactions validated together, and "delete all transactions of an asset in an account". | Every invalid situation must be fixable from the UI. |
| D-07 | **Deleting is a real delete** with an entry in the internal audit log. | Backup/export already allow recovery; a recycle bin adds complexity. |
| D-08 | **No `INITIAL_POSITION` type.** `BUY`, `SELL`, `DIVIDEND`, `COUPON` have an `affects_cash` flag (default true). A guided flow "Add holdings I already own" creates BUYs with `affects_cash = false`. | Allows full history reconstruction, real dates and lots, and CSV import with or without cash flows. |
| D-14 | **Transaction date = operation date.** The value (settlement) date is kept as metadata. Same-day ordering is deterministic (see domain-rules §4). | Verified on real broker exports: only operation date keeps cash non-negative. |
| D-15 | **Cost basis is stored in account currency**, as actually paid. | Gain includes the FX effect, like the broker shows. |
| D-32 | **Exact decimal arithmetic everywhere; quantities support at least 8 decimals.** No binary floating point for money or quantities. | Correctness; cheap now, costly later. |

## Metrics

| ID | Decision | Rationale |
|---|---|---|
| D-10 | **Percentages:** unrealized % = unrealized € / cost basis of open positions; realized % = realized € / cost basis of the quantity sold (lifetime). Income (dividends/coupons) is shown in € only. | Each percentage has a simple, hand-verifiable base. |
| D-11 | **Invested capital = cost basis of open positions.** Cash is part of portfolio value but is excluded from returns. | The user wants the return on invested money, not on idle cash. |
| D-12 | **Every gain is shown gross and net of taxes.** Taxes are *recorded* from broker data, never *calculated*. Unrealized gain is the same gross and net (no estimated tax). The dashboard highlights the net figure. | Matches what the investor really kept. |
| D-17 | **Bonds:** quantity = nominal value; price = % of nominal; valued at clean price (accrued interest not included). | Standard quoting; limitation is disclosed in the UI. |
| D-18 | **Accrued interest paid and taxes are fields of the transaction** (`accrued_interest_paid` on BUY; signed `taxes` on BUY and SELL; `taxes` on DIVIDEND/COUPON). Capital gains tax is a field of SELL, not a separate transaction. | Importers can attach broker side-rows by order reference; per-asset net figures work. |
| D-19 | **Bond maturity/redemption in V1 is entered as a SELL at 100% of nominal.** | No real redemption sample exists yet. |

## Market data

| ID | Decision | Rationale |
|---|---|---|
| D-16 | **Market data providers are pluggable.** FX: ECB reference rates via Frankfurter (no key). Prices: a default keyless provider plus one optional keyed provider configurable from the admin area. Daily closes stored locally. Missing-data rules in domain-rules §12 (carry-forward, stale flag, valuation at cost, manual price). | Free price sources are unreliable and may break; local history and fallbacks contain the damage. |

## Import / export

| ID | Decision | Rationale |
|---|---|---|
| D-20 | **V1 import:** Directa Excel export (`.xlsx`) + the app's own documented CSV format (also the export format). Fineco and other brokers are V2. Only movement types verified on real exports are recognized; anything else is listed as "not recognized" and not imported. Nothing is applied without preview and explicit confirmation. | Never guess broker formats. |
| D-20b | **Duplicate detection:** order reference for trades, protocol for other movements. Already-imported rows are skipped by default. | Two different orders can have identical date/ISIN/quantity (verified). |
| D-29 | **Real broker exports and personal numbers never enter the public repository**, not even anonymized. Only synthetic fixtures are committed. | The repository is public. |

## Security and operations

| ID | Decision | Rationale |
|---|---|---|
| D-21 | **Auth:** first user becomes Administrator via setup wizard; no public registration; users created by the admin; Argon2id, min 12 chars; opaque revocable session tokens (cookie for web, bearer for future clients); password reset by admin (admin recovery via documented container command); TOTP 2FA **optional** with 10 recovery codes (mandatory option in V2); login rate limiting; roles fixed: Investor, Administrator. | Works without a mail server; same model for future mobile clients. |
| D-21b | **The Administrator cannot read other users' portfolios.** | Privacy by design. |
| D-21c | **Provider credentials are encrypted at rest** with a key from an environment variable, or generated at first start and stored in the data volume; never returned by the API after saving; **excluded from backups.** | A restored instance asks for them again. |
| D-22 | **Backup/restore is for the whole instance**, done by the Administrator, as a single versioned archive with a manifest; optional passphrase encryption. Per-user export is CSV only. Per-user backup/restore is V2. | Merging into a non-empty instance is the risky part of restore. |
| D-23 | **Deployment:** one container, **amd64 only in V1** (arm64 in V2/V3), one mounted data volume (`/data`: database, backups, keys, attachments), configuration via environment variables, non-root with configurable UID/GID, docker-compose example in the README. | Restoring = new container + same volume. |
| D-24 | **Non-functional targets** in requirements.md §Non-functional. | Make "light" verifiable. |
| D-25 | **License: AGPL v3.** The UI links to the source code. | Protects a self-hosted project from closed SaaS forks. |
| D-26 | **UI languages: Italian and English; light and dark theme.** Texts live outside the code (i18n). | Predispose for more languages. |
| D-27 | **Roles are fixed in V1: Investor and Administrator.** Custom roles are V2. | Keeps authorization simple. |
| D-30 | **Documentation, code, comments and commit messages are in English.** | Open-source project. |
