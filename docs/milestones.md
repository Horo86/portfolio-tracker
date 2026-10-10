# Implementation Milestones (V1)

Each milestone is one or more short Claude Code sessions with this cycle: **plan (plan mode) → approval → implementation → tests → commit → verification by the maintainer**. A milestone is done when its "Done when" items pass and the docs are updated if a decision changed. Start a fresh session per milestone; `CLAUDE.md` and `docs/` carry the context.

Suggested model use: strongest model for M0 architecture decisions and M1/M2/M7 (calculation, data model, backup); a lighter model is fine for UI and routine CRUD.

| # | Milestone | Content | Done when |
|---|---|---|---|
| M0 | Architecture and skeleton | Write ADRs (see `architecture/README.md`); project skeleton; Docker image and compose example; `/data` volume layout; health endpoint; migration framework with pre-migration copy; CI (lint, test, build image) | `docker compose up` starts an empty app, health OK, CI green, ADRs merged |
| M1 | Calculation engine | Pure domain library, no UI/DB: positions, PMC, realized/unrealized, income, taxes, gross/net, splits, bonds, cash, ordering, validation (negative quantity), exact decimals | Every example in `domain-rules.md` §16 is an automated test; property/edge tests for ordering and edits |
| M2 | Data model and API | Schema and migrations; accounts, assets, transactions CRUD; validation and recalculation on every change; bulk edit; delete-all-for-asset; audit log entries; OpenAPI | API tests cover E6/E7 and the no-deadlock operations; derived data rebuildable from sources |
| M3 | Import | Importer interface; Directa parser per `import/directa.md`; app CSV import/export; preview and confirm; duplicates; "do not move cash" option | Synthetic fixture matches `directa_synthetic_2024.expected.json`; re-import imports nothing; optional local test on real files in `private/` |
| M4 | Market data | Provider interface; FX (Frankfurter); default keyless price provider + optional keyed provider; scheduler; local history; missing/stale rules; manual prices; asset search | Rules of `domain-rules.md` §12 tested with fake providers; coverage check with real ISINs run by the maintainer |
| M5 | Web UI | Dashboard, accounts, asset detail, transaction entry/list/edit, "Add holdings I already own", import preview, i18n (it/en), light/dark, accessibility | Mockups in `docs/ui/` implemented; WCAG AA checks on main flows |
| M6 | Users and security | Setup wizard, users, Argon2id, sessions, rate limiting, TOTP 2FA with recovery codes, admin area (users, providers with encrypted credentials), roles | Security checklist in `CLAUDE.md` satisfied; admin cannot read other users' data (tested) |
| M7 | Backup, export, release | Whole-instance backup/restore archive with manifest and optional encryption; CSV export; docs (README, env vars, backup format); release pipeline | Restore on a fresh container reproduces state; upgrade test with pre-migration copy; tagged release image published |

## Notes
- Do the real-data checks (import of the maintainer's own exports, price coverage of real ISINs) locally with files under `private/`; never commit them or their figures.
- Anything not specified in the docs: stop and ask, then record the answer in `docs/decisions.md`.
