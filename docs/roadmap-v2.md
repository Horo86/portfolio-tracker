# Roadmap — Beyond V1

Items deliberately left out of V1 to keep the first release small, with the reason. V1 is designed so none of these requires a redesign (see "Prepared in V1").
Order is not a commitment.

## Metrics
- **Annualized return, XIRR, TWRR** — introduced together. Needs: external cash-flow definition (deposits/withdrawals), treatment of holdings entered with a start date, edge cases (periods < 30 days, non-convergence, zero positions, missing prices). Plain-language explanations in the UI.
- Dividend yield on cost; gain filters by period (e.g. realized in the last year).
- Historical FX attribution in the reporting currency (convert realized gains, income and taxes at the rate of their date; split FX effect from price effect).
- Benchmark comparison.

## Assets and operations
- Custom assets (manual pricing).
- Directly held cryptocurrencies (wallets, exchanges, crypto-to-crypto swaps).
- Multi-currency accounts.
- Transfers between accounts (transfer-in/out preserving cost basis).
- Corporate actions beyond splits: mergers, spin-offs, conversions, ISIN/ticker changes.
- Typed account expenses (stamp duty, Tobin tax, custody fees) with automatic conversion of imported withdrawals (original broker type is already in metadata).
- Separation of revaluation from coupon for inflation-linked bonds.
- Separate tax field (e.g. taxes_on_accrued) for withholding on accrued interest received on SELL, to be defined once real broker samples of bond sales exist.
- Account archiving (hide a closed account while keeping its history).

## Data and import
- Fineco and other broker importers (each broker is a separate parser).
- Directa: SELL, withdrawals, redemptions, foreign-currency trades, foreign dividends, once real samples exist.
- Geographic allocation (needs ETF composition data).
- **Borsa Italiana price adapter** (MOT/EuroTLX bonds such as BTPs, which the default provider often lacks; until then bonds use manual prices, D-118). One adapter module behind the provider interface (D-117).
- **OpenFIGI** for ISIN → listing mapping, only if the M4 coverage check shows Yahoo search by ISIN is not enough (D-118).
- A real keyed price provider adapter (the framework, order and credential store are in V1, D-117, D-124).
- Split-unadjusted price history, or reversing provider split adjustments from the recorded splits (D-126), if not solved before the M5 value-history chart.
- Attach PDF statements ("contabili") to transactions; storage under `/data/attachments`; decide whether CSV export excludes them and whether the full backup includes them always or on request. (Later idea: read a statement to pre-fill a transaction.)

## Security and administration
- Mandatory 2FA option set by the Administrator; passkeys/WebAuthn.
- **E-mail address and recovery by e-mail** (optional SMTP). The e-mail becomes a separate, optional user field: the username stays the login identifier (D-89) and is never replaced by the e-mail; login accepts either the username or the e-mail; a forgotten password can be reset through a link sent by e-mail. Until then, recovery is the administrator reset or the `reset-password` container command (D-93).
- Custom roles; family/household sharing between users.
- Audit log browsing UI.
- Per-user backup and restore (merge into an existing instance); restore into an instance that already has users.
- Scheduled automatic backup (e.g. nightly, with a retention count). V1 has manual backup from the admin area only.
- "Delete my data" in the user settings (a user erases their own accounts, transactions, manual assets and manual prices).

## User interface
- Dark theme (V1 has four light color themes; colors are CSS variables, so a dark theme is a further set of values).
- Mobile layout (phones, tablets); reference mockup in `ui/screens/v2-dashboard-mobile.png`.
- Number and date format setting separate from the language. In V1 formats follow the interface language (it/en).

## Platform
- arm64 container image (Raspberry Pi, ARM NAS).
- Native mobile apps (API-first design already allows it).
- Notifications and alerts.

## Prepared in V1 (so that the above is cheap later)
- Stable UUIDs; transaction `metadata` keeps the original broker type; `external_ref` stored.
- Backup is a single archive with a manifest and a format version; `/data/attachments` reserved.
- Importers are plug-in parsers behind one interface; market data providers are pluggable.
- Colors defined as design tokens (CSS variables) per theme, see `ui/README.md`.
- Translations kept out of code; asset type, transaction type and provider lists are extensible enums.
- Exact decimals and ≥ 8-decimal quantities.
