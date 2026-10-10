# CLAUDE.md — Portfolio Tracker

Self-hosted, open-source (AGPL v3) investment tracker for private investors: a visual dashboard to monitor ETFs, stocks and bonds across broker accounts. Monitoring only, no trading. This repository is **public**.

## Status
Requirements for V1 are complete; **no technology is chosen yet and no code exists.** Work proceeds by milestones in `docs/milestones.md` (M0 first, then M0.5 mockups).

## Source of truth (read before working)
- `docs/requirements.md` — what V1 does and the non-functional targets
- `docs/domain-rules.md` — every calculation rule, field rule and worked example (§16 = acceptance tests)
- `docs/domain-model.md` — entities and fields
- `docs/decisions.md` — why things are the way they are (D-xx)
- `docs/import/directa.md`, `docs/import/app-csv.md` — import specs
- `docs/roadmap-v2.md` — what is deliberately NOT in V1
- `docs/architecture/` — ADRs (to be written in M0); `docs/ui/` — mockups (to be added)

If a document is ambiguous or silent, **stop and ask the maintainer**; then record the answer in `docs/decisions.md` or the relevant doc. Never silently invent a rule. Docs change in the repo, not in chat.

## Hard rules
1. **Privacy of the public repo.** Never commit, paste into docs/issues/commits/tests, or quote real financial data: no real broker exports (even anonymized), no real ISIN-holdings combinations, balances, or personal figures. Real files live only in `private/` (git-ignored) and are used only by local, optional tests that skip when the folder is empty. Fixtures and examples use invented data.
2. **Money and quantities are exact decimals.** Never binary floating point. Quantities support ≥ 8 decimals. Round only for display.
3. **Transactions and market data are the only sources of truth.** Positions, cash, valuation and metrics are derived and must be reproducible; there must be a rebuild path.
4. **Calculation code is test-first.** Every example in `domain-rules.md` §16 is an automated test; any change to a rule updates the doc and the tests in the same commit.
5. **Never guess broker formats.** Importers recognize only verified movement types; the rest is reported as not recognized (see `docs/import/directa.md`).
6. **Never apply imported data without preview and explicit confirmation.**
7. **V1 stays small.** Do not implement anything listed in `docs/roadmap-v2.md` or "Out of scope" in the requirements. Prepare extension points only where the docs say so (stable UUIDs, extensible enums, pluggable providers/importers, i18n, versioned backup archive).
8. **Taxes are recorded from broker data, never calculated.**
9. **No telemetry or outbound calls** other than the configured market data providers.

## Working method
- Start each task in **plan mode**: read the relevant docs, propose a plan (files, steps, tests), wait for approval.
- Small, reviewable steps; run the tests before declaring anything done; commit at green with a Conventional Commit message (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- A milestone may span several sessions; never two milestones in one session. When a milestone finishes, update `docs/milestones.md` status and note any decision in `docs/decisions.md`.
- Prefer the simplest maintainable solution (see "Simplicity Principle" in the requirements). No microservices, no extra infrastructure.
- Code, comments, docs and commit messages are in **English**. UI texts live in translation files (Italian and English), never hard-coded.

## Key domain conventions (details in `docs/domain-rules.md`)
- Transaction types: BUY, SELL, DIVIDEND, COUPON, DEPOSIT, WITHDRAWAL, STOCK_SPLIT. `affects_cash` flag (default true) on BUY/SELL/DIVIDEND/COUPON.
- Average cost (PMC): BUY fees in cost; SELL cost removed = cost basis × qty sold / qty held (never the rounded PMC); remaining = previous − removed. Derived divisions: 8 decimals, half-even (§0).
- Bonds: quantity = nominal, price = % of nominal, clean price. Accrued interest paid (BUY, −) / received (SELL, +) counts in income and cash, never in cost.
- `taxes` signed (positive withheld, negative credit). BUY taxes (bonds only) and income taxes → income; SELL taxes → realized. Gain shown gross and net.
- Transaction date = operation date; `value_date` is a column. Same-day order: untimed by type priority, then timed by time, then insertion (§4).
- Editing/deleting: reject only if some quantity would go negative; negative cash = warning; atomic edit, bulk edit and delete-all-for-asset must exist (no deadlocks).
- Percentages: unrealized % on open cost; realized % gross and net on cost sold; total gain and income in € only. No XIRR/TWRR in V1.
- No price → valued at cost; only missing FX makes totals partial. Account figures in account currency; portfolio converted at latest/daily rate.

## Security checklist (V1)
Argon2id passwords (min 12 chars); opaque revocable sessions stored hashed; login rate limiting (trusted proxies via env var); TOTP optional with recovery codes; backend authorization on every endpoint filtered by owner (from M2); no UI/API path for the Administrator to other users' portfolios, manual assets or manual prices (no cryptographic isolation: recommend backup encryption); restore only from the setup wizard on an instance with no users; provider credentials encrypted at rest, never returned by the API, excluded from backups; secure cookies and security headers; no default credentials; dependency license check (AGPL-compatible).

## Deployment shape
One container, one data volume `/data`, env-var configuration, logs to stdout, non-root with configurable UID/GID, linux/amd64 only in V1, health endpoint, automatic migrations with a database copy beforehand. The README carries the suggested `docker-compose.yml`.

## Commands
To be filled in during M0 (install, test, lint, run, build image). Keep this section accurate; it is the first thing to update when tooling is chosen.

## Layout
```
docs/            requirements, domain, decisions, roadmap, import specs, architecture (ADRs), ui
tests/fixtures/  synthetic fixtures only (directa/ has a synthetic Excel + expected JSON)
private/         git-ignored; maintainer's real files for local optional checks
```
Source directories are created in M0 according to the ADRs.
