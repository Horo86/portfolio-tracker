# ADR-0007: Market data adapters

Status: accepted (M0, October 2026) — design only, built in M4

## Context
D-16, D-35, D-44, D-46; `../domain-rules.md` §9 and §12. Free sources are unreliable; providers must be replaceable without code changes in other modules; no outbound calls other than configured providers.

## Options considered
- **Small in-house adapter interfaces** — each provider is one module; easy to swap; no dependency on a multi-provider SDK.
- **A third-party aggregation library** (one package wrapping many sources) — convenient, but couples the app to that library's scraping logic and release cycle; many are unofficial and break silently.
- **A paid data vendor as the only source** — reliable but needs keys and money from every self-hoster: incompatible with "works without configuration".

## Decision
- Two small interfaces in the backend: `PriceProvider` (search by ISIN/ticker, daily closes for a date range) and `FxProvider` (daily reference rates for a date range). Each adapter is a separate module registered by name; the admin area enables them and stores credentials encrypted (ADR-0005, D-21c).
- FX: **Frankfurter** (ECB reference rates, no key); cross rates through EUR.
- Prices: one default keyless adapter plus one optional keyed adapter; the concrete providers are chosen in M4 after a coverage check with the maintainer's ISINs (done locally, never committed).
- Isolation: per-call timeouts, retries with back-off, failures recorded per asset; a failing provider only stops new prices.
- Data stored locally (daily close, quote unit, source); manual prices per user override provider prices for that user only.

## Consequences
- Easier: adding or replacing a provider touches one module.
- Harder: provider symbol mapping per asset must be stored (asset `provider-specific symbols`).
