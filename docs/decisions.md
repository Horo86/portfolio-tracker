# Decision Log

All decisions below were taken during V1 requirements definition (October 2026) and are **accepted**. D-33 onwards record the resolutions of the V1 requirements review (October 2026).
Other documents are the detailed source; this log explains *what was decided and why*.
To change a decision, add a new row that supersedes the old one (do not delete history).

## Scope

| ID | Decision | Rationale |
|---|---|---|
| D-01 | **Custom assets are out of V1.** | Need manual pricing, irregular price series and a dedicated UI. Listed instruments cover the target use case. |
| D-02 | **Cryptocurrencies held directly are out of V1.** Listed crypto ETC/ETN products are normal instruments (type `ETF`). | Different data sources and logic (wallets, crypto-to-crypto swaps). |
| D-03 | **One cash currency per account.** A broker with several cash currencies is modelled as several accounts. | Keeps cash, cost basis and valuation simple. |
| D-04 | **Transfers between accounts are out of V1.** To move holdings, enter a BUY on the new account with `affects_cash = false` and the original average cost. *Refined by D-47.* | See D-08. |
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
| D-14 | **Transaction date = operation date.** The value (settlement) date is kept as metadata. Same-day ordering is deterministic (see domain-rules §4). *Value date: superseded by D-48. Ordering: refined by D-45.* | Verified on real broker exports: only operation date keeps cash non-negative. |
| D-15 | **Cost basis is stored in account currency**, as actually paid. | Gain includes the FX effect, like the broker shows. |
| D-32 | **Exact decimal arithmetic everywhere; quantities support at least 8 decimals.** No binary floating point for money or quantities. | Correctness; cheap now, costly later. |

## Metrics

| ID | Decision | Rationale |
|---|---|---|
| D-10 | **Percentages:** unrealized % = unrealized € / cost basis of open positions; realized % = realized € / cost basis of the quantity sold (lifetime). Income (dividends/coupons) is shown in € only. *Refined by D-41.* | Each percentage has a simple, hand-verifiable base. |
| D-11 | **Invested capital = cost basis of open positions.** Cash is part of portfolio value but is excluded from returns. | The user wants the return on invested money, not on idle cash. |
| D-12 | **Every gain is shown gross and net of taxes.** Taxes are *recorded* from broker data, never *calculated*. Unrealized gain is the same gross and net (no estimated tax). The dashboard highlights the net figure. | Matches what the investor really kept. |
| D-17 | **Bonds:** quantity = nominal value; price = % of nominal; valued at clean price (accrued interest not included). *Refined by D-43.* | Standard quoting; limitation is disclosed in the UI. |
| D-18 | **Accrued interest paid and taxes are fields of the transaction** (`accrued_interest_paid` on BUY; signed `taxes` on BUY and SELL; `taxes` on DIVIDEND/COUPON). Capital gains tax is a field of SELL, not a separate transaction. *Refined by D-42 and D-43.* | Importers can attach broker side-rows by order reference; per-asset net figures work. |
| D-19 | **Bond maturity/redemption in V1 is entered as a SELL at 100% of nominal.** | No real redemption sample exists yet. |

## Market data

| ID | Decision | Rationale |
|---|---|---|
| D-16 | **Market data providers are pluggable.** FX: ECB reference rates via Frankfurter (no key). Prices: a default keyless provider plus one optional keyed provider configurable from the admin area. Daily closes stored locally. Missing-data rules in domain-rules §12 (carry-forward, stale flag, valuation at cost, manual price). *Refined by D-35 (price visibility) and D-46 (valuation without a price).* | Free price sources are unreliable and may break; local history and fallbacks contain the damage. |

## Import / export

| ID | Decision | Rationale |
|---|---|---|
| D-20 | **V1 import:** Directa Excel export (`.xlsx`) + the app's own documented CSV format (also the export format). Fineco and other brokers are V2. Only movement types verified on real exports are recognized; anything else is listed as "not recognized" and not imported. Nothing is applied without preview and explicit confirmation. *Refined by D-38 (app CSV) and D-49 (Directa).* | Never guess broker formats. |
| D-20b | **Duplicate detection:** order reference for trades, protocol for other movements. Already-imported rows are skipped by default. *Refined by D-38.* | Two different orders can have identical date/ISIN/quantity (verified). |
| D-29 | **Real broker exports and personal numbers never enter the public repository**, not even anonymized. Only synthetic fixtures are committed. | The repository is public. |

## Security and operations

| ID | Decision | Rationale |
|---|---|---|
| D-21 | **Auth:** first user becomes Administrator via setup wizard; no public registration; users created by the admin; Argon2id, min 12 chars; opaque revocable session tokens (cookie for web, bearer for future clients); password reset by admin (admin recovery via documented container command); TOTP 2FA **optional** with 10 recovery codes (mandatory option in V2); login rate limiting; roles fixed: Investor, Administrator. *Refined by D-51.* | Works without a mail server; same model for future mobile clients. |
| D-21b | **The Administrator cannot read other users' portfolios.** *Restated by D-33.* | Privacy by design. |
| D-21c | **Provider credentials are encrypted at rest** with a key from an environment variable, or generated at first start and stored in the data volume; never returned by the API after saving; **excluded from backups.** | A restored instance asks for them again. |
| D-22 | **Backup/restore is for the whole instance**, done by the Administrator, as a single versioned archive with a manifest; optional passphrase encryption. Per-user export is CSV only. Per-user backup/restore is V2. *Refined by D-34.* | Merging into a non-empty instance is the risky part of restore. |
| D-23 | **Deployment:** one container, **amd64 only in V1** (arm64 in V2/V3), one mounted data volume (`/data`: database, backups, keys, attachments), configuration via environment variables, non-root with configurable UID/GID, docker-compose example in the README. | Restoring = new container + same volume. |
| D-24 | **Non-functional targets** in requirements.md §Non-functional. | Make "light" verifiable. |
| D-25 | **License: AGPL v3.** The UI links to the source code. | Protects a self-hosted project from closed SaaS forks. |
| D-26 | **UI languages: Italian and English; light and dark theme.** Texts live outside the code (i18n). | Predispose for more languages. |
| D-27 | **Roles are fixed in V1: Investor and Administrator.** Custom roles are V2. *Refined by D-39.* | Keeps authorization simple. |
| D-30 | **Documentation, code, comments and commit messages are in English.** | Open-source project. |

## Review resolutions (V1 requirements review)

| ID | Decision | Rationale |
|---|---|---|
| D-33 | **Restates D-21b.** The application gives the Administrator no UI or API to read other users' portfolios, but there is **no cryptographic isolation**: whoever has access to the server, the `/data` volume or a backup can read the data. Backup passphrase encryption stays optional but is recommended; the UI and the README say so. | Honest privacy statement; per-user encryption is out of proportion for V1. |
| D-34 | **Restore is possible only from the first-run setup wizard**: the choice "Restore from backup" replaces "Create first administrator". "Empty instance" = no users exist. No restore into an instance that already has users in V1. | Removes the contradiction between "empty instance" and "the Administrator restores from the UI"; merging is the risky part (D-22). |
| D-35 | **Prices and asset visibility.** Provider prices are shared across users; manual prices belong to the user who entered them and never affect other users. Each user sees only the assets they use or added; asset search never reveals which ISINs other users hold. Provider-resolved assets are shared reference data; manually created assets (D-37) are private to their creator, so the same ISIN may exist once as a shared asset and once per user as a private one. | One user's manual price or hand-typed metadata must not change another user's figures, and the asset list must not leak holdings. |
| D-36 | **Reporting currency different from the account currency.** Per-account figures are always in the account currency. The aggregated portfolio converts the already-computed account figures at the latest available rate for current values and at each day's rate for the historical series. No historical FX attribution in V1 (e.g. a realized gain is converted at today's rate, not at the rate of the sale day); the limitation is documented in the UI help. | Simple, verifiable, consistent with D-15; attribution needs a dedicated design. |
| D-37 | **Manual asset creation.** When providers cannot resolve an instrument, the user can create the asset by hand (ISIN, name, asset type, quote currency, quote unit). The asset-discovery principle becomes: the user is not required to type metadata **when it can be fetched**. | Many bonds are not covered by free providers; manual entry must not be blocked. |
| D-38 | **App CSV round trip.** The CSV gains `id` (transaction UUID), `metadata` (JSON), `account_currency` and `account_provider`; the column `note` is renamed `notes`. Duplicate detection: by `id` first, then by `external_ref`. `id` is matched only against the importing user's own transactions; an `id` that already exists for another user is replaced by a new one, without revealing anything. Accounts are mapped to existing ones or created from the file in the preview (all created on a fresh instance). A CSV with several accounts creates one import batch per account. | Export → import must reproduce the data without duplicates and without losing the original broker type (D-13). |
| D-39 | **Administrator with a portfolio; account lifecycle.** The Administrator also has Investor rights on their own data. Accounts are editable (name, provider); the cash currency is locked after the first transaction; an account can be deleted (with its transactions) after confirmation, with an audit log entry. Archiving accounts is V2. | In a household the administrator is usually also an investor; deleting a mistaken account must be possible. |
| D-40 | **Single cost-removed formula on SELL:** `cost removed = cost basis × quantity sold / quantity held`, rounded as in `domain-rules.md` §0. The rounded PMC is for display only and is never used in calculations. | Two formulas gave different results once the PMC is rounded. |
| D-41 | **Percentages.** Realized % is defined gross and net, both on the cost of the quantity sold: gross = realized gross / cost removed; net = (realized gross − SELL taxes) / cost removed. Unrealized % is unchanged (D-10). Total gain is shown in € only (gross and net); income stays in € only. | Every percentage keeps a simple base; a total-gain % would need a base that mixes open and closed positions. |
| D-42 | **Where taxes land.** BUY `taxes` are allowed only on bonds (credits on accrued interest or issue discount) and count as **income taxes**. DIVIDEND/COUPON `taxes` count as income taxes. SELL `taxes` count as **realized taxes**, including any withholding on accrued interest received (known simplification: the net total is unaffected). | Removes the E3/E4 ambiguity with one rule per transaction type. |
| D-43 | **New SELL field `accrued_interest_received`** (bonds only, ≥ 0, default 0): it is gross income of the asset, it is part of the cash movement, and it does not affect cost basis or realized gain. | Secondary-market bond sales credit accrued interest; without the field the SELL invariant could not match the broker. |
| D-44 | **Quotes in minor currency units** (e.g. GBX, pence): asset quote unit `MINOR_CURRENCY_UNIT`; the quote currency is the major currency (e.g. GBP) and the price is divided by 100 before FX conversion. | London-listed instruments are quoted in pence; otherwise values would be 100× too high. |
| D-45 | **Same-day ordering:** transactions without `trade_time` first, by type priority (`DEPOSIT`, `STOCK_SPLIT`, `BUY`, `DIVIDEND`/`COUPON`, `SELL`, `WITHDRAWAL`); then transactions with `trade_time`, by time; remaining ties by insertion sequence. | The previous rule did not say how timed and untimed transactions mix. |
| D-46 | **Valuation without a price.** On any day for which an asset has no price on or before that day (never priced, or before its first price), the position is valued at that day's cost basis and flagged "estimated at cost". Charts are never blocked by missing prices; only missing FX makes a total or a chart partial. | Charts must not disappear because one bond has no data source. |
| D-47 | **Transfer workaround (refines D-04):** on the source account a `SELL` with `affects_cash = false` whose `gross_amount` is the exact cost removed (zero fees and taxes, so zero gain); on the destination account a `BUY` with `affects_cash = false` at the same cost. Known limitation: the transfer adds its cost to the realized % base of the source with zero gain. | Moves a holding without inventing a realized gain or a cash flow. |
| D-48 | **`value_date` is a real column of the transaction** (optional, informational), not metadata. Supersedes the "kept as metadata" part of D-14; the transaction date remains the operation date. | It is present in every broker export and is useful for reconciliation. |
| D-49 | **Directa import clarifications.** The option "I did not record the deposits: do not move cash" also skips `DEPOSIT` rows and account-expense `WITHDRAWAL` rows by default (each individually selectable in the preview). An `Acquisto` without an order reference is "not recognized". The asterisk in `Bollo portafoglio titoli*` is a literal character. Importing a Directa file into an account whose currency is not EUR is a blocking error. A merged transaction takes the insertion sequence of its oldest row (last row in file order). | Closes the gaps found in the review without guessing broker behaviour. |
| D-50 | **Milestones.** Basic authentication (setup wizard, login, sessions) and ownership filtering move into M2; 2FA, the admin area and hardening stay in M6. UI mockups are an explicit step (M0.5) before M2. A milestone may span several sessions; never two milestones in one session. | The API must be owner-filtered from the first endpoint; the screens shape the API. |
| D-51 | **Admin area in V1:** users, market data providers, backup/restore, session duration, login lockout thresholds. Trusted proxies for rate limiting are configured by an environment variable (list of networks), not in the UI. | Defines "security settings" and "system configuration"; per-IP limits behind a proxy need trusted forwarded headers. |
| D-52 | **Minor cases.** A DIVIDEND or COUPON on a closed position (quantity 0) is allowed. Cash paid in lieu of split fractions is entered as a SELL of the fractional quantity. | Common real cases that the rules did not state. |

## Technology (M0)

| ID | Decision | Rationale |
|---|---|---|
| D-53 | **Technology stack (M0).** Backend: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite in WAL mode under `/data`; uv, ruff, pytest. Frontend: React + TypeScript + Vite, i18next (it/en), Apache ECharts (from M5), light/dark themes; built as static files served by the backend (Node only at build time). One linux/amd64 container, non-root, `/data` volume, env-var configuration, JSON logs on stdout. Money and quantities stored as TEXT decimals and computed with Python `Decimal` only. Later: argon2-cffi, pyotp, openpyxl, APScheduler. Details, alternatives and pinned versions: ADR-0001 … ADR-0011 in `architecture/`. | Decided with the maintainer: small footprint, exact decimals in the standard library, generated OpenAPI, one container. Rejected: Java/Spring Boot (RAM, image size), Go, Node backend, PostgreSQL (second container), htmx (tables and charts). |

## Calculation engine (M1)

| ID | Decision | Rationale |
|---|---|---|
| D-54 | **Property tests use `hypothesis`** (dev dependency only, pinned exactly, license checked like every other dependency). It is added when the property tests are written, after the maintainer's go-ahead. | ADR-0010 requires property tests for ordering and edits; generated inputs with shrinking find edge cases that hand-picked examples miss. |
| D-55 | **Valuation is part of the M1 engine as pure functions**: prices and FX rates are passed in memory; the engine applies quote units, carry-forward, "estimated at cost" (D-46) and conversion to the reporting currency (D-36). Providers, stored history, the scheduler and the stale-price flag stay in M4. | The §16 examples E3, E4, E11 and E12 need valuation; keeping it pure keeps it testable without a database. |
| D-56 | **Quantity after a stock split** = `quantity × new / old`, rounded to 8 decimals half-even like every other derived division (`domain-rules.md` §0). | One rounding rule in the engine; quantities already support 8 decimals. |
| D-57 | **Percentages are returned by the engine as ratios** (e.g. `0.10000000` for +10%), rounded to 8 decimals half-even; when the base is zero the value is "not available" (`None`), never 0 and never an error. The UI multiplies by 100 and rounds for display. | One representation for all percentages; a zero base has no meaningful percentage. |
| D-58 | **FX conversion rounds once**: the converted amount is rounded to 8 decimals half-even; no intermediate rounding (a cross rate between two non-EUR currencies is not rounded on its own). | Avoids compounding rounding errors; consistent with §0. |
| D-59 | **DIVIDEND or COUPON on an asset never held in that account** is accepted **with a warning** (not rejected), because the recorded history may be incomplete. On a position that was held and is now closed it is accepted without a warning (D-52). | Do not block users with partial histories, but surface a likely missing BUY. |
| D-60 | **Transaction invariants are checked with exact `Decimal` comparison**, no tolerance (`4889.90` equals `4889.900`; `4889.91` does not). | Amounts come from the broker or the user exactly; a tolerance would hide entry errors. |
| D-61 | **Negative cash is checked at the end of each day**: the warning reports the first date whose end-of-day balance is below zero; a dip inside a day that is recovered the same day does not warn. | Matches §11 ("cash balance at a date = Σ up to and including that date"); same-day order is not a reliable view of the broker's intraday cash. |
| D-62 | **Fields not listed for a type in `domain-rules.md` §2 are rejected** (e.g. `quantity` on a COUPON, `fees` on a DIVIDEND); the §2 table is a closed list. **To revisit in M3**: whether a COUPON may carry the nominal as informational `quantity` when an importer provides it. | Never accept data the rules do not define; the import specs will show whether the nominal is needed. |
| D-63 | **Selling the whole position removes exactly the remaining cost basis** instead of applying the D-40 formula, so a closed position has cost 0 even when the cost has more than 8 decimals. With ≤ 8 decimals the result is identical to D-40. | A closed position must never keep a rounding residue. |
| D-64 | **Missing FX gives a partial total, not an unavailable one.** The affected position's value is unavailable; the account's securities value, total value and unrealized (€ and %, base = cost of the valued positions only) sum the valued positions and carry a `partial` flag. **M2/M5 must show a clearly visible "partial" label next to every total that carries the flag.** | Never invent numbers (§12) but keep the rest of the account visible; the label prevents reading a partial figure as complete. |
| D-65 | **Total gain of a partial account** (gross and net) uses the unrealized of the valued positions only; realized and income do not depend on prices and stay complete. The `partial` flag propagates to the gain figures (same visible label, D-64). | Consistent with D-64. |
| D-66 | **Bond average cost:** the engine returns the PMC per unit of nominal in account currency (e.g. `0.976`); the API (M2) multiplies by 100 to show it as % of nominal (`97.60%`), like other display conversions. | The engine has one PMC definition for every asset; display formats belong to the API/UI. |
