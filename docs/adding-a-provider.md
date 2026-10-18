# Adding a market data engine

An **engine** is one provider adapter: a module in `backend/src/portfolio_tracker/marketdata/` that fetches prices, exchange rates or security facts from one source. Adding an engine changes **no** other part of the app: not the calculation engine, the API or the data model (D-117, D-131). This guide describes the contract fixed by ADR-0007 (second M4 amendment).

## 1. Check the source first

- Read the provider's **current** documentation and terms of use. Never write an adapter from memory or from a blog post.
- Note the endpoints, parameters, response shapes, error answers, rate limits, history depth and whether the API is official. Put these in the module docstring, with the month you checked them (see `frankfurter.py` and `yahoo.py`).
- If the API is unofficial or undocumented, say so in the docstring and mark the engine as unofficial (step 3). Shapes you assume rather than observe are listed as assumed.
- No new dependency: HTTP goes through the shared helper on the standard library. If a dependency seems unavoidable, ask the maintainer first (license check, CLAUDE.md).

## 2. Write the adapter

Create `marketdata/<name>.py`. The name is lower case, at most 32 characters (`[a-z][a-z0-9_-]*`).

- Implement the protocol of each role the engine serves (`marketdata/base.py`):
  - price roles (`ETF_STOCK`, `BOND`): `search(query)` and `daily_closes(symbol, start, end)`;
  - `FX`: `rates(currencies, start, end)`, in units per 1 EUR;
  - `CLASSIFY`: `classify(isin)`, returning the price class and identifiers (ticker, exchange), or nothing.
- Use the shared HTTP helpers in `marketdata/http.py`: the `Transport` given to the factory, `check_status` (maps 404, 429 with `Retry-After`, and other errors) and `parse_json` (exact decimals, bounded numbers, no NaN). Never call `urllib` directly.
- Wrap every call in the engine's throttle (`throttle.call(...)`), so the queue and the asset search share one rate limit.
- Raise `ProviderError` with a stable `ErrorCode`. A shape you do not expect is `FORMAT_CHANGED`; an unknown symbol is `NOT_FOUND`. Any other exception escaping the adapter is recorded as `ADAPTER_ERROR` (the engine counts as failing for that instrument; only the exception type is logged), so a bug in one engine never stops a run. Error details never contain a credential, a URL with a credential, or a response body.
- Normalise currencies with `units.normalize_currency` (pence `GBp`/`GBX` become `GBP` with `MINOR_CURRENCY_UNIT`). An unknown currency code is refused, never guessed.
- Return closes with the currency and unit of **that answer** (`CloseSeries`), so a change on the provider's side is caught (D-119).
- A price engine may use the `hints` of a search query (identifiers from a classifier) and map them to its own symbols, for example an exchange code to its symbol suffix. This mapping stays inside the adapter, which also checks that what it finds is on the same exchange and of the same kind as the hint (see `yahoo.py`, D-136).
- Each listing reports its exchange's name and code (`exchange`, `exchange_code`), compared with the preferred exchanges and the main exchanges. Rank the candidates with `ranking.candidate_rank` **before** checking them, and return them in that rank, so a cap on checks never leaves out a main exchange (D-125).
- Each listing reports `last_close_day`, the day of the latest close seen when it was found, and the `hint` it came from, if any. `marketdata/acceptance.py` then applies the currency, unit, class and recent-close rules to every candidate.

## 3. Declare what the engine can do

Each engine registers one `ProviderSpec` (`marketdata/registry.py`): its name, its factory, its throttle policy and a `Capabilities` value (`marketdata/base.py`, D-131) with these fields:

| Field | Example (Yahoo) | Effect |
|---|---|---|
| `roles` | `ETF_STOCK`, `CLASSIFY` | the orders the administrator may put it in |
| `search_isin`, `search_text` | yes, yes | ISIN search links instruments; text search serves the asset search box |
| `echoes_isin` | no | whether its identifiers can anchor another engine's match (D-133) |
| `units` | `CURRENCY`, `MINOR_CURRENCY_UNIT` | a listing in another unit is refused; `BOND` needs `PERCENT_OF_NOMINAL` |
| `history_years` | none (provider's earliest) | first fetch of a new series, within the 20-year cap (D-122) |
| `needs_credential`, `credential_label` | no | a keyed engine is built only with its credential |
| `official`, `terms_url` | unofficial | shown to the administrator (M6) |
| `main_exchanges` | `MIL`, `GER`, `LSE`, …, `NYQ`, `NMS`, … | its exchange codes that rank first when no preferred exchange decides (D-125) |

The throttle policy (Yahoo: at most 1 call a second) goes in the spec; the registry keeps one throttle per engine name. `Capabilities` refuses contradictions, such as a `BOND` engine without `PERCENT_OF_NOMINAL`. Declare only what you verified. A unit you are not sure about is left out: assets then keep their manual prices instead of being valued 100 times off.

## 4. Register it

Add one `registry.register(ProviderSpec(...))` call to `default_registry()` in `marketdata/registry.py`. This is the only change outside the new module.

On an existing instance, a new engine **starts disabled** (D-132). The administrator enables it and places it in the orders (admin area, M6). On a fresh instance, the environment variables can list it (`PT_PRICE_PROVIDERS`, `PT_BOND_PRICE_PROVIDERS`, `PT_FX_PROVIDERS`).

## 5. Test it with fixtures

Tests never use the network: the guard in `backend/tests/conftest.py` fails any test that tries to connect.

- Save synthetic responses as `tests/fixtures/marketdata/<name>_*.json`, with the provider's real shape but **invented** values: ISINs with country code `ZZ`, invented symbols and names, no real prices. Describe each fixture in `tests/fixtures/marketdata/README.md`, and say which parts of the shape were observed and which are assumed.
- Put the tests in `backend/tests/marketdata/test_marketdata_<name>.py`. Drive the adapter through `FakeTransport` with `fixture_response`, `json_response`, `queue` and `timeout_error` from `backend/tests/marketdata/fakes.py`, and a throttle with a fake clock (no real sleeping).
- Cover at least:
  - a search hit and an empty search;
  - closes or rates over a range, with gaps (weekends, holidays) and dates at the range edges;
  - 404, 429 with `Retry-After`, a 5xx error and a timeout;
  - malformed JSON and a changed shape (`FORMAT_CHANGED`);
  - currency and unit normalisation, including pence where the source has it;
  - exact numbers: decimals stay as received, and nothing passes through binary floating point.
- If the engine adds a role, add one test showing a fake instrument resolved through that role.

## 6. Credentials

- Set `needs_credential=True` in its `Capabilities`. The factory receives the decrypted value in `ProviderContext.credential`; without one, the engine is skipped and reported, never failing the others.
- Send it in a header when the provider allows it. A credential in the URL must never reach a log or an error.
- In tests, put a fake value through the credential store and assert with `caplog` that it never appears in the logs.
- Administrators set the value through the write-only admin route (D-135, M6). It is encrypted at rest (D-124), never returned, and excluded from backups.

## 7. Finish

- Add the engine to ADR-0007 (what it serves, official or not, what was verified).
- Document new environment variables, if any, in `README.md`.
- Run `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` in `backend/`.
