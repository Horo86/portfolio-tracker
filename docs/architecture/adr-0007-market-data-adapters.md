# ADR-0007: Market data adapters

Status: accepted (M0, October 2026) — **amended in M4** (D-116–D-119, D-122–D-125)

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

### M4 amendment
- **Package** `backend/src/portfolio_tracker/marketdata/`: protocols, typed results (`Listing`, `Close`, `ProviderError` with stable codes), a registry by name, and one HTTP helper on the standard library (`urllib`: timeouts, response size cap, no redirect to another host) with an **injectable transport**, so tests use synthetic fixtures and never the network (D-117).
- **Order by configuration:** `PT_PRICE_PROVIDERS` (default `yahoo`), `PT_FX_URL`, `PT_MARKET_DATA=off` (D-123). The admin area for providers and credentials comes in M6; until then the credential store (D-124) has no route. A real keyed adapter comes later; a fake keyed provider registered only in the tests proves the plug-in rule end to end.
- **FX:** Frankfurter, endpoints and limits verified against its current documentation when the adapter is written (M4 step 5) and recorded here; rates are stored per currency as units per EUR (cross rates through EUR, as before).
- **Prices: Yahoo Finance (unofficial)** is the default, confirmed or dropped at M4 checkpoint B (D-118): throttled, back-off honouring `Retry-After`, raw daily `close` (split-adjusted, D-126). **OpenFIGI is not used in M4.**
- **Storage:** provider data belongs to internal **instruments** (ISIN + quote currency, D-125) and their provider **listings**, not to assets; `Asset.provider_symbols` stays unused. Users' assets link to an instrument (D-116); instruments are never exposed by the API.
- **Units:** a listing is used only when compatible with the asset: exact GBp/GBX ↔ GBP rescale by rule table, otherwise refused with a "price source mismatch" status (D-119).
- **Fetch ranges:** from the last stored day of each series; full history for a new instrument (D-122).

## Consequences
- Easier: adding or replacing a provider touches one module and one configuration value.
- Harder: an unofficial default source may break without notice; the stale notice, local history and manual prices contain the damage. Listing choice per instrument is stored and must be re-resolved when a provider drops a symbol.
