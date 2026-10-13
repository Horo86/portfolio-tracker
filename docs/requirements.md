# Portfolio Tracker — Requirements (V1)

Version: 1.2 (review resolutions D-33..D-52; see `decisions.md`)
Status: Approved for the architecture and design phase
Related: `domain-model.md`, `domain-rules.md`, `roadmap-v2.md`, `import/`, `milestones.md`

---

# Vision

A self-hosted investment tracking platform that lets non-professional investors understand the performance of their investments in a simple, visual and intuitive way. It focuses on **monitoring**, not trading. It is web-first and API-first so that native mobile apps can follow later.

The product answers one question: **"How are my investments performing?"**

# Product Philosophy

- Clarity over complexity; no financial-terminal feel; avoid jargon.
- Visualizations before tables and reports.
- Users must understand: how much they invested, how much they gained or lost (gross and net of taxes), which assets contribute, and how the portfolio evolved.
- **V1 principle:** the minimum that answers the question, built on foundations that allow adding features without rewrites. Predispose (stable IDs, extensible types, pluggable providers/importers, i18n, versioned backup); implement only what V1 needs.

# Core Principles

Self-hosted first · Open source (AGPL v3) · Data ownership · Privacy by design · Modern, visual-first UX · Mobile-ready architecture · Long-term maintainability · Low operational complexity and resource use · Container-friendly deployment · Backup and restore as first-class features.

# Users and Personas

Primarily private, non-professional investors; multi-user support is for families, households and small groups. Not for professional managers, banks, brokers or institutions.

- **Investor** — views performance, manages accounts, enters/imports transactions, reviews history, income and taxes withheld.
- **Administrator** — first user of a new installation; manages users, market data providers, backup/restore and security settings (see Administration). May also have their own portfolio, with Investor rights on their own data (D-39). **The application gives the Administrator no UI or API to read other users' portfolios** (see Privacy model).
- **System Administrator** — infrastructure only (container, reverse proxy/TLS, monitoring, upgrades, infrastructure backups). Must not need access to financial data.

# Functional Scope (V1)

## Supported assets
ETFs (including ETC/ETN listed products), stocks, bonds. **Not in V1:** custom assets, directly held crypto (D-01, D-02).

## Supported operations
Buy · Sell · Dividend · Bond coupon · Cash deposit · Cash withdrawal · Stock split / reverse split.
Buy, sell, dividend and coupon have an **"does not move cash"** option (D-08). Commissions are part of buy/sell. Account-level expenses (stamp duty, Tobin tax…) are entered as withdrawals with a note (D-13). Taxes withheld by the broker are recorded, never calculated.
Detailed field rules: `domain-rules.md` §2.

## Accounts and portfolio
- Multiple investment accounts per user (name, provider, one cash currency each). Name and provider are editable; the currency is locked once the account has a transaction; an account can be deleted with its transactions after confirmation (audit log entry). Archiving is V2 (D-39).
- Single-account view and aggregated portfolio view.
- Starting from an existing portfolio: guided flow **"Add holdings I already own"** (multi-row), plus an optional first-run prompt after creating an account.

## Transaction management
- Manual entry with computation helpers; list with filters; edit; delete.
- Editing/deleting is validated on the final state; negative quantity is a blocking error, negative cash a warning; bulk edit and "delete all transactions of an asset in an account" must exist so that any situation can be fixed from the UI (`domain-rules.md` §4).
- All derived data is recalculated after every change.

## Dashboard
The primary screen. Historical value and current value get the most prominent area. It shows:
- Total portfolio value (securities + cash, cash visible separately)
- Invested capital (cost basis of open positions)
- Gain/loss gross and net of taxes, split into unrealized and realized: total gain in € only; unrealized and realized in € and % (realized % gross and net, D-41); income in €; taxes withheld in €
- Historical portfolio evolution (ranges: 1M, 6M, 1Y, 3Y, 5Y, max)
- Allocation by asset, by asset type, by currency
- Best and worst performers (unrealized %)
- Passive income history (dividends and coupons by month/year)
Geographic allocation is V2.

## Asset detail
Per asset: position, average cost, value, unrealized/realized gain (gross and net), **dividends/coupons received since first held** (gross, taxes, net), also for closed positions, price chart, transactions.

## Performance metrics
Absolute and percentage figures as defined in `domain-rules.md` §5, §6, §14. Annualized return, XIRR and TWRR are V2. Each metric is explained in user-friendly language in the UI.

## Market data
- Daily updates, no real-time data. Daily closing prices (date, close, currency) and FX history stored locally.
- Multiple currencies with automatic conversion for valuation. Per-account figures are in the account currency; the aggregated portfolio converts them to the reporting currency at the latest rate (current figures) and at each day's rate (history). No historical FX attribution in V1 (D-36, `domain-rules.md` §9).
- Pluggable providers: FX from ECB reference rates (Frankfurter, no key); prices from a default keyless provider plus an optional keyed provider configurable in the admin area (D-16).
- Missing/stale data behaviour, manual price entry: `domain-rules.md` §12. Provider prices are shared across users; manual prices are per user and never affect other users (D-35). Assets without any price are valued at cost, so charts are never blocked by missing prices (D-46).
- Provider credentials are managed in the admin UI and never need code changes.

## Asset discovery
Search and add assets by ISIN or ticker; metadata fetched automatically when possible; if several matches, the user chooses; ISIN preferred; the user is not required to type metadata that can be fetched. Asset type is proposed and confirmed. If no provider resolves a listed instrument, the user can create the asset by hand (ISIN, name, type, quote currency, quote unit); such an asset is private to that user (D-37, D-35). This is not a custom asset: non-listed custom assets remain out of scope. Each user sees only the assets they use or added; search never reveals which ISINs other users hold.

## Data entry and import
- Manual entry.
- Import from file: **Directa Excel export** and the **app's own CSV format** (also the export format). Other brokers (Fineco…) are V2. Specs: `import/directa.md`, `import/app-csv.md`.
- Import is never applied automatically. Flow: (1) validate the file; (2) identify assets; (3) show validation issues; (4) highlight ambiguities; (5) show a preview, including skipped duplicates and not-recognized rows; (6) explicit user confirmation. Option to import without moving cash.
- Only movement types verified on real exports are recognized; anything else is reported, never guessed.

# User Experience

Simplicity, clarity, modern design, accessibility (WCAG 2.1 AA on the main flows), interactive charts, Italian and English. Charts are preferred over large tables. Screens follow the mockups in `ui/`.

- **Desktop layout only** in V1. The mobile layout is V2 (`ui/screens/v2-dashboard-mobile.png` is kept as a reference); the API-first design does not depend on it.
- **Light theme only**, with four color themes selectable per user: Sobrio, **Moderno** (default), Ambra, Grafite (tokens in `ui/README.md`). The dark theme is V2.
- Number and date formats follow the interface language (it/en); a separate format setting is V2.

## User settings
Profile, password, two-factor authentication, reporting currency (default EUR), language, color theme.

# Administration

Dedicated area (D-51): user management (create, disable, reset password, disable 2FA), market data providers, backup and restore, security settings (session duration, login lockout thresholds). Other system configuration is done through environment variables. Audit information is recorded internally (browsing UI is V2).

# API-First Architecture

All functionality is exposed through documented APIs (OpenAPI). The web app uses the same APIs as future clients (iOS/Android/tablet/integrations). Business logic does not depend on the frontend. V1 ships only the web app.

# Authentication and Security (D-21)

- Enforced by the backend. First launch: the setup wizard either creates the first user as Administrator or restores a backup (D-34); no default credentials; no public registration; users are created by the Administrator.
- Passwords: Argon2id, minimum 12 characters, no composition rules; temporary passwords force a change at first login.
- Sessions: opaque, revocable tokens stored hashed; cookie for web, bearer for future clients; "log out everywhere".
- Password recovery without email: Administrator resets a user's password; documented container command for the Administrator's own recovery.
- Two-factor authentication: TOTP, **optional** per user; QR code and text key; activation confirmed with a code; 10 single-use recovery codes; Administrator can disable it for a locked-out user; disabling requires password + code. Mandatory option in V2.
- Login rate limiting per account and per IP with growing delays; failed attempts logged. Behind a reverse proxy the client IP is taken from forwarded headers only for trusted proxies, configured by an environment variable (list of networks) (D-51).
- Authorization: every datum belongs to a user and the backend always filters by user. Roles fixed in V1: Investor, Administrator.
- Provider credentials encrypted at rest (key from an environment variable, or generated at first start in the data volume), never returned by the API, excluded from backups.
- TLS is the System Administrator's responsibility (reverse proxy); the app works behind a proxy with secure cookies and security headers.

## Privacy model (D-33)
The application gives the Administrator no UI or API to read other users' portfolios. There is **no cryptographic isolation** between users: whoever has access to the server, the `/data` volume or a backup can read all data. Backup encryption with a passphrase is optional but recommended; the UI and the README say so.

# Data Ownership, Export and Portability

Data is never locked into a proprietary format. Each user can export their data as CSV (the app CSV format, importable again). The backup format is documented.

# Backup and Restore (D-22)

- Application-level backup is mandatory, performed by the Administrator in the UI. Infrastructure backups remain the System Administrator's job.
- One backup = the **whole instance** as a single versioned, portable, documented archive with a manifest. Optional passphrase encryption, recommended; unencrypted backups are flagged in the UI because anyone holding the file can read all data (D-33).
- Contents: users (including password hashes and 2FA data), accounts, assets, transactions, historical prices, FX, preferences, application settings, audit log. **Excluded:** provider credentials.
- A backup must suffice to recreate the complete application state on a fresh installation; restore never requires database access.
- Restore is available only in the first-run setup wizard, as the alternative to "Create first administrator". An empty installation = no users exist. No restore into an instance that already has users in V1 (D-34).
- Per-user backup/restore is V2.

# Deployment (D-23)

- Container deployment is mandatory (Docker, Docker Compose). **amd64 only in V1.**
- One application container; **one mounted data volume (`/data`)** holds the database, backups, keys and (V2) attachments. Starting a new container on the same volume restores everything.
- No other state outside the volume; logs on standard output; configuration through documented environment variables; no source change is ever needed for deployment.
- Non-root user with configurable UID/GID.
- The README includes the suggested `docker-compose.yml`, the volume layout and the update procedure.
- Health-check endpoints.

# Upgrade Safety

Pull the new image, restart, continue. Automatic data migrations. A copy of the database is made before every migration. User data is preserved.

# CI/CD

Automated pipeline: validation (lint, tests), build, Docker image build, publish, versioned releases, minimal manual steps; reproducible build. A license check on dependencies (AGPL compatibility).

# Non-functional Requirements (V1 targets)

| Area | Target |
|---|---|
| Reference size | up to 10 users; 5,000 transactions per user; 200 distinct assets; 20 years of daily prices |
| Footprint | single container; idle memory < 256 MB; image < 200 MB; startup < 10 s |
| Performance | dashboard < 2 s at reference size; after a transaction change, recalculation and refreshed views < 2 s |
| Browsers | latest two versions of Chrome, Firefox, Safari, Edge; desktop layout (mobile layout is V2) |
| Accessibility | WCAG 2.1 AA on login, dashboard and transaction entry |
| Platform | linux/amd64 |
| Observability | health endpoint, structured logs on stdout, no outbound telemetry |
| Quality | the calculation engine is covered by automated tests derived from `domain-rules.md` §16 and the synthetic fixtures; calculation changes require tests |
| Documentation | README, deployment guide, full environment-variable list, backup format specification, OpenAPI |
| Source of truth | exact decimal arithmetic, no floating point for money/quantities |

# Architecture Constraints and Simplicity Principle

Decisions are driven by simplicity, maintainability, community adoption, self-hosting friendliness, ease of backup and recovery, low operational complexity. The simplest maintainable solution that meets the requirements is preferred; microservices, Kubernetes, service meshes, distributed caches and event streaming only when clearly justified. An embedded single-file database is expected to be the natural fit with the single-volume model (to be confirmed in the architecture decision).

# Open Source and Privacy of Contributions

License: **AGPL v3**; the UI links to the source code. The build is reproducible; documentation supports contributors. The repository is public: **no real financial data, broker exports (even anonymized) or personal figures may be committed**; tests use synthetic fixtures only.

# Explicitly Out of Scope (V1)

Order execution · Trading · Financial advice · Copy trading · Social investing · Public portfolio sharing · Benchmark comparison · Current/savings accounts · Real estate · Liabilities and loans · Notifications and alerts · Tax calculation · Custom assets · Direct crypto · Multi-currency accounts · Transfers between accounts · Annualized return/XIRR/TWRR · Geographic allocation · Attachments · Account archiving · Dark theme · Mobile layout · Scheduled automatic backup · Number/date format setting separate from the language · "Delete my data" for a user · Historical FX attribution in the reporting currency · Restore into an instance that already has users.

See `roadmap-v2.md` for what comes next.
