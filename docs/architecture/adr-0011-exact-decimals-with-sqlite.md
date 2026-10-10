# ADR-0011: Exact decimals with SQLite

Status: accepted (M0, October 2026) — implemented in M2 with the first table; engine rules in M1

## Context
Money and quantities must be exact; quantities need ≥ 8 decimals; money keeps the precision given by the broker; derived divisions round **half-even at 8 decimals** (`../domain-rules.md` §0; D-32, D-40). SQLite has no exact decimal storage type: a `NUMERIC`/`REAL` column stores binary floating point.

## Options considered
- **TEXT with canonical decimal strings** — exact; any scale (keeps the broker's precision, e.g. 2 decimals for amounts, 8+ for quantities); human-readable in backups and SQL; SQLite cannot do arithmetic on it, which the design does not need (all arithmetic is in Python).
- **Scaled integers** (e.g. value × 10^8 in INTEGER) — exact and sortable, but a fixed scale either wastes range or loses precision, and every reader must know the scale.
- **REAL / NUMERIC affinity** — rejected: binary floating point.

## Decision
- A dedicated SQLAlchemy `TypeDecorator` (`DecimalText`) stores `decimal.Decimal` values as **TEXT** canonical strings (no exponent, no float conversion) and returns `Decimal`. Passing a `float` raises an error. Columns that need ordering or range queries on amounts are not expected; if one appears, a companion sortable column is added explicitly.
- The calculation engine uses **Python `Decimal` only**, with a local context; every derived division is quantized to 8 decimals with `ROUND_HALF_EVEN` (one helper, used everywhere). Sums and differences are exact.
- Pydantic schemas accept and emit decimal **strings** in the API (ADR-0004); the frontend never does money arithmetic with JavaScript numbers.
- Tests: round-trip of representative values (e.g. `0.1`, `33.33333333`, `-2.44`, 20-digit values) through the database, and rejection of floats.

## Consequences
- Easier: no precision surprises anywhere in the stack; backups are readable.
- Harder: aggregation in SQL (`SUM`) on these columns is not allowed — aggregation happens in the engine; this matches "transactions are the source of truth, derived data is computed".
