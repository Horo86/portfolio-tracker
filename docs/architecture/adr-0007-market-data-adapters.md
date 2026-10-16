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
- **FX: Frankfurter API v2** (verified against https://frankfurter.dev and its public responses, October 2026): `GET {PT_FX_URL}/v2/rates?providers=ecb&base=EUR&quotes=XXX&from=…&to=…` returns a JSON array of `{date, base, quote, rate}`, one row per ECB publication day (none on weekends and holidays); ECB data starts on 1999-01-04; a currency without data is absent and an unknown code is HTTP 422. v1 is deprecated (kept by Frankfurter). The documentation states no numeric rate limit and no maximum range, and asks clients to cache. The adapter sends one request per currency (an unknown code affects only that currency), throttled to at most one call a second. Rates are stored per currency as units per EUR (cross rates through EUR, as before).
- **Throttle** (`marketdata/throttle.py`): one per provider, shared by the queue and searches; minimum interval between calls, pause on 429/5xx/network errors (`Retry-After` or a doubling back-off, capped at 15 minutes), and refusal after 5 consecutive failures until the next run.
- **Prices: Yahoo Finance (unofficial)** is the default, confirmed or dropped at M4 checkpoint B (D-118): throttled, back-off honouring `Retry-After`, raw daily `close` (split-adjusted, D-126). **OpenFIGI is not used in M4.** Shapes checked against public responses in October 2026 (there is no documentation): search (`/v1/finance/search`) returns symbols, exchanges and `quoteType` but no currency, so each candidate's currency comes from a short chart call (at most 6 per search); the chart (`/v8/finance/chart/{symbol}`) gives `meta.currency` (`GBp` for London pence), the exchange time zone (closes are dated in it) and closes serialised as float32 (read back as the shortest equivalent decimal). The closes are returned with the currency and unit of that answer, so a provider-side unit change is caught (D-119). Yahoo listings never claim `PERCENT_OF_NOMINAL`: bond quoting is unverified, so bonds keep manual prices until the coverage check says otherwise.
- **Storage:** provider data belongs to internal **instruments** (ISIN + quote currency, D-125) and their provider **listings**, not to assets; `Asset.provider_symbols` stays unused. Users' assets link to an instrument (D-116); instruments are never exposed by the API.
- **Units:** a listing is used only when compatible with the asset: exact GBp/GBX ↔ GBP rescale by rule table, otherwise refused with a "price source mismatch" status (D-119).
- **Fetch ranges:** from the last stored day of each series; a new series (instrument or FX currency) from the earliest date the provider offers, capped at 20 years (D-122).

## Consequences
- Easier: adding or replacing a provider touches one module and one configuration value.
- Harder: an unofficial default source may break without notice; the stale notice, local history and manual prices contain the damage. Listing choice per instrument is stored and must be re-resolved when a provider drops a symbol.
