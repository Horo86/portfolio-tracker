# Roadmap — Beyond V1

Items deliberately left out of V1 to keep the first release small, with the reason. V1 is designed so none of these requires a redesign (see "Prepared in V1").
Order is not a commitment.

## Metrics
- **Annualized return, XIRR, TWRR** — introduced together. Needs: external cash-flow definition (deposits/withdrawals), treatment of holdings entered with a start date, edge cases (periods < 30 days, non-convergence, zero positions, missing prices). Plain-language explanations in the UI.
- Dividend yield on cost; gain filters by period (e.g. realized in the last year).
- Benchmark comparison.

## Assets and operations
- Custom assets (manual pricing).
- Directly held cryptocurrencies (wallets, exchanges, crypto-to-crypto swaps).
- Multi-currency accounts.
- Transfers between accounts (transfer-in/out preserving cost basis).
- Corporate actions beyond splits: mergers, spin-offs, conversions, ISIN/ticker changes.
- Typed account expenses (stamp duty, Tobin tax, custody fees) with automatic conversion of imported withdrawals (original broker type is already in metadata).
- Separation of revaluation from coupon for inflation-linked bonds.

## Data and import
- Fineco and other broker importers (each broker is a separate parser).
- Directa: SELL, withdrawals, redemptions, foreign-currency trades, foreign dividends, once real samples exist.
- Geographic allocation (needs ETF composition data).
- Attach PDF statements ("contabili") to transactions; storage under `/data/attachments`; decide whether CSV export excludes them and whether the full backup includes them always or on request. (Later idea: read a statement to pre-fill a transaction.)

## Security and administration
- Mandatory 2FA option set by the Administrator; passkeys/WebAuthn.
- Password recovery by email (optional SMTP).
- Custom roles; family/household sharing between users.
- Audit log browsing UI.
- Per-user backup and restore (merge into an existing instance).

## Platform
- arm64 container image (Raspberry Pi, ARM NAS).
- Native mobile apps (API-first design already allows it).
- Notifications and alerts.

## Prepared in V1 (so that the above is cheap later)
- Stable UUIDs; transaction `metadata` keeps the original broker type; `external_ref` stored.
- Backup is a single archive with a manifest and a format version; `/data/attachments` reserved.
- Importers are plug-in parsers behind one interface; market data providers are pluggable.
- Translations kept out of code; asset type, transaction type and provider lists are extensible enums.
- Exact decimals and ≥ 8-decimal quantities.
