# Architecture

Status: **no technology decided yet.** The first task of milestone M0 is to decide the items below and record each as an ADR (`adr-NNNN-title.md` in this folder). Requirements and constraints are in `../requirements.md`; do not decide by habit — compare against the criteria.

## Decision criteria (from the requirements)
Simplicity · maintainability · community adoption · self-hosting friendliness · ease of backup/recovery · low operational complexity · small image (< 200 MB) and low memory (< 256 MB idle) · exact decimal arithmetic · API-first (OpenAPI) · reproducible build · AGPL-compatible dependencies.

## Constraints already fixed
- One application container, one data volume `/data` (database, backups, keys, future attachments); logs to stdout; configuration by environment variables; non-root with configurable UID/GID; linux/amd64 in V1.
- API-first: the web app consumes the same documented API as future clients.
- Derived data (positions, cash, valuation) is reproducible from transactions and market data.
- Pluggable market data providers and importers behind interfaces.
- Whole-instance backup as a single archive with manifest and format version.
- Secrets: provider credentials encrypted at rest, key from env var or generated into the volume.

## ADRs to write
| ADR | Topic | Notes |
|---|---|---|
| 0001 | Backend language/framework | Decimal support, ecosystem for scheduling, auth, spreadsheet reading, OpenAPI |
| 0002 | Database and migrations | Expected: embedded single-file database; safe online backup; pre-migration copy |
| 0003 | Frontend stack and charting | Responsive, accessible, i18n, light/dark; served by the same container |
| 0004 | API style and documentation | OpenAPI as the contract; versioning |
| 0005 | Authentication/session implementation | Opaque revocable sessions, Argon2id, TOTP, rate limiting |
| 0006 | Background jobs / scheduler | Daily price and FX updates inside the same container |
| 0007 | Market data adapters | Frankfurter FX; keyless default price provider; optional keyed provider; failure isolation |
| 0008 | Backup archive format | Manifest, version, optional encryption, reserved attachments folder |
| 0009 | Build, CI/CD and release | Image build, tests, license check, versioning |
| 0010 | Repository layout and test strategy | Monorepo vs split; engine tests from `domain-rules.md` §16 |

## ADR template
```
# ADR-NNNN: Title
Status: proposed | accepted | superseded by ADR-XXXX
Context: what forces the decision (link requirements/decisions)
Options considered: with pros/cons against the criteria
Decision: what and why
Consequences: what becomes easier/harder
```
