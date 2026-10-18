# ADR-0007: Market data adapters

Status: accepted (M0, October 2026) — **amended in M4** (D-116–D-119, D-122–D-125, D-130; engines and routing: D-131–D-136)

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
- **Order by configuration:** `PT_PRICE_PROVIDERS` (default `yahoo`), `PT_FX_URL`, `PT_MARKET_DATA=off` (D-123); since the second amendment these are initial values seeded into the database (D-132). The admin area for providers and credentials comes in M6; until then the credential store (D-124) has no route. A real keyed adapter comes later; a fake keyed provider registered only in the tests proves the plug-in rule end to end.
- **FX: Frankfurter API v2** (verified against https://frankfurter.dev and its public responses, October 2026): `GET {PT_FX_URL}/v2/rates?providers=ecb&base=EUR&quotes=XXX&from=…&to=…` returns a JSON array of `{date, base, quote, rate}`, one row per ECB publication day (none on weekends and holidays); ECB data starts on 1999-01-04; a currency without data is absent and an unknown code is HTTP 422. v1 is deprecated (kept by Frankfurter). The documentation states no numeric rate limit and no maximum range, and asks clients to cache. The adapter sends one request per currency (an unknown code affects only that currency), throttled to at most one call a second. Rates are stored per currency as units per EUR (cross rates through EUR, as before).
- **Throttle** (`marketdata/throttle.py`): one per provider, shared by the queue and searches; minimum interval between calls, pause on 429/5xx/network errors (`Retry-After` or a doubling back-off, capped at 15 minutes), and refusal after 5 consecutive failures until the next run.
- **Prices: Yahoo Finance (unofficial)** is the default for ETFs and stocks, confirmed after the coverage check (D-118): throttled, back-off honouring `Retry-After`, raw daily `close` (split-adjusted, D-126). OpenFIGI classifies ISINs for it (D-136, below). Shapes checked against public responses in October 2026 (there is no documentation): search (`/v1/finance/search`) returns symbols, exchanges and `quoteType` but no currency, so each candidate's currency comes from a short chart call (at most 6 per search); the chart (`/v8/finance/chart/{symbol}`) gives `meta.currency` (`GBp` for London pence), the exchange time zone (closes are dated in it) and closes serialised as float32 (read back as the shortest equivalent decimal, D-130). The closes are returned with the currency and unit of that answer, so a provider-side unit change is caught (D-119). Yahoo listings never claim `PERCENT_OF_NOMINAL`: bond quoting is unverified, so bonds keep manual prices.
- **Listing choice (D-125):** within an engine, the candidates are ranked before any is checked: the exchanges of `PT_PRICE_EXCHANGES` (code or name; empty by default), then the engine's main exchanges in its declared order (Yahoo: the exchange table of amendment 2, then NYSE, Nasdaq, NYSE Arca and NYSE American), then any other venue; the first accepted candidate is the listing. Neither a user's ticker or name nor the provider's result order takes part. Linking always goes through the queue.
- **Storage:** provider data belongs to internal **instruments** (ISIN + quote currency, D-125) and their provider **listings**, not to assets; `Asset.provider_symbols` stays unused. Users' assets link to an instrument (D-116); instruments are never exposed by the API.
- **Units:** a listing is used only when compatible with the asset: exact GBp/GBX ↔ GBP rescale by rule table, otherwise refused with a "price source mismatch" status (D-119).
- **Fetch ranges:** from the last stored days of each series (the last 5 fetched again); a new series (instrument or FX currency) from the earliest date the provider offers, capped at 20 years (D-122).

### M4 amendment 2: engines, capabilities and routing (D-131–D-136)
- **One spec per engine** with declared capabilities (D-131): roles (`ETF_STOCK`, `BOND`, `FX`, `CLASSIFY`), ISIN and text search, whether listings carry the ISIN back, units, history depth, credential, official or not, throttle policy. Protocols: `PriceProvider.search` takes a query (an ISIN or a text, plus optional hints from a classifier); `daily_closes` and `FxProvider.rates` are unchanged; a new `Classifier` returns the price class and identifiers for an ISIN.
- **Configuration in the database** (D-132): engine on/off, order per role, preferred exchanges; seeded from the environment only while nothing is stored; an administrator command resets them from the environment after confirmation; `PT_MARKET_DATA=off` always wins; engines added by an upgrade start disabled.
- **Routing** (D-133): the instrument's price class comes from classifier engines, otherwise from the first accepted listing (the `BOND` order is tried before `ETF_STOCK`); never from a user's asset. Accepting a listing checks currency, the declared unit, the unit against the class, and that the match came from an ISIN.
- **Switching engines** (D-134): make before break, a level check when engines are reordered, no level check when the previous engine was disabled, a purge on request, and no series stitched from two engines.
- **Admin API** (D-135): shape fixed now, built in M6; counts only, credentials write only.
- **OpenFIGI, the first classifier** (D-118, D-136). Verified on 17 October 2026 against its documentation, its terms and live answers for public ISINs (none from users' data):
  - `POST https://api.openfigi.com/v3/mapping` takes an array of jobs `{"idType": "ID_ISIN", "idValue", "micCode"?}` and answers one entry per job: `{"data": [...]}`, `{"warning": "No identifier found."}` or `{"error": "..."}`; an ISIN with an unknown country (`ZZ...`) gets `{"error": "Invalid idValue format."}`, so test ISINs never reach a real answer.
  - Each result has `figi`, `name`, `ticker`, `exchCode` (Bloomberg's own exchange code, never interpreted here), `securityType`, `securityType2`, `marketSector` and other FIGIs; **no ISIN and no MIC** come back. `marketSector` values: `Comdty`, `Corp`, `Curncy`, `Equity`, `Govt`, `Index`, `M-Mkt`, `Mtge`, `Muni`, `Pfd`. An ETF is `ETP` / `Equity`; a BTP is `Govt` / `Govt`, and its "ticker" is a description, not a trading symbol.
  - Without a key: 25 requests a minute (`ratelimit-policy: 25;w=60`), 10 jobs per request; 429 carries `retry-after`; too many jobs is 413. With a key (`X-OPENFIGI-APIKEY`, later): 250 a minute, 100 jobs.
  - Terms (last updated 27 November 2018): FIGI identifiers are public domain, free for any use including commercial; no warranty; the names "FIGI" and "OpenFIGI" must not suggest endorsement. The terms say nothing on API limits or caching. Use by self-hosted instances, each calling from its own server, is assumed allowed.
  - **Exchange table** (MIC filter → Yahoo suffix → exchange name in Yahoo's chart answer). MICs checked in the ISO 10383 registry (downloaded 17 October 2026); suffixes from Yahoo Help "Exchanges and data providers on Yahoo Finance"; each row was checked live with a public security, through OpenFIGI with that MIC and Yahoo's chart for ticker + suffix:

    | MIC (ISO 10383) | Exchange | Yahoo suffix | Yahoo `exchangeName` |
    |---|---|---|---|
    | `MTAA` | Euronext Milan (Borsa Italiana) | `.MI` | `MIL` |
    | `XETR` | Xetra | `.DE` | `GER` |
    | `XLON` | London Stock Exchange | `.L` | `LSE` |
    | `XAMS` | Euronext Amsterdam | `.AS` | `AMS` |
    | `XPAR` | Euronext Paris | `.PA` | `PAR` |
    | `XSWX` | SIX Swiss Exchange | `.SW` | `EBS` |
    | `XMAD` | Bolsa de Madrid | `.MC` | `MCE` |
    | `XBRU` | Euronext Brussels | `.BR` | `BRU` |
    | `XWBO` | Wiener Börse | `.VI` | `VIE` |

    Left out: `XMIL`, the operating MIC of Borsa Italiana (the filter found neither an ETF nor a BTP; its segment `MTAA` found both); `XFRA` (`.F`, `FRA`) and `XLIS` (`.LS`, `LIS`), which work but do not fit in one request; `XDUB` (Bloomberg's ticker differs from Yahoo's symbol); `ETLX` (thin trading on Yahoo). `XLON` also returns tickers of alternative trading lines that Yahoo does not know; they are tried after the shorter ones and refused by Yahoo.
- **How to add an engine:** `../adding-a-provider.md`.

## Consequences
- Easier: adding or replacing a provider touches one module and one registration line; the administrator enables and orders it per price class (M6), and separate engines can serve ETFs and stocks and bonds.
- Harder: an unofficial default source may break without notice; the stale notice, local history and manual prices contain the damage. Listing choice per instrument is stored and must be re-resolved when a provider drops a symbol.
- Harder: switching engines must keep each series from one source (D-134); a shorter history from the new engine means fewer provider prices for earlier days.
