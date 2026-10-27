# <img src="docs/ui/icon.svg" alt="Portfolio Tracker icon: the letters P and T joined, with a small rising line" width="40" height="40"> Portfolio Tracker

A self-hosted, open-source investment tracker for private investors. It answers one question: **"How are my investments performing?"** with a visual, jargon-free dashboard. Monitoring only — no trading.

> **Status: early development, not usable yet.** Milestones M0 (architecture and skeleton), M1 (calculation engine), M2 (database, API and basic authentication), M3 (import) and M4 (market data) are done: with the API (`/api/docs`) you can set up the instance, log in, manage users, accounts, assets, manual prices and transactions, read account and portfolio summaries and their breakdown (allocation, dividends and coupons by month), import Directa Excel exports and the app's own CSV (preview, then confirm), export your transactions as CSV, and search for securities; daily closes and ECB exchange rates are downloaded in the background (or not at all with `PT_MARKET_DATA=off`). The web UI (M5) is being built in seven slices: the first four are done (first run, login, accounts, settings, Italian and English, the four color themes; transactions: the list with filters, entry and editing of all seven types with a live summary, bulk edit and delete, securities created by hand; import of Directa Excel exports and the app's CSV, with a preview to check and tick before confirming, and a summary; holdings: the holdings list and each holding's detail, showing where the prices of every security stand, the pages to add a security (search by ISIN for automatic prices, or by name, or create it by hand) and to enter the holdings you already own in one go, your own prices of a security entered by hand, and editing or deleting a security; the dashboard with its figures and tables, the charts still to come); everything else is reachable through the API only for now. The UI mockups (M0.5) are in [`docs/ui/`](docs/ui/). See [`docs/milestones.md`](docs/milestones.md).

## Planned V1 highlights
- ETFs, stocks and bonds across several broker accounts, with an aggregated portfolio view
- Manual entry, and import from a Directa Excel export or the app's own CSV format (always with preview and confirmation)
- Gain/loss shown **gross and net of taxes**, unrealized and realized, plus dividends and coupons received
- Daily prices and FX stored locally; historical evolution charts and allocations
- Multi-user (families/households), optional two-factor authentication, Italian and English UI, four light color themes (desktop layout; dark theme and mobile layout come later)
- Whole-instance backup from the UI and restore from the first-run setup wizard; CSV export (re-importable) for every user
- One container, one data volume, low resource use

What is intentionally **not** in V1 (crypto, custom assets, XIRR/TWRR, Fineco import, …) is listed in [`docs/roadmap-v2.md`](docs/roadmap-v2.md).

## Documentation
| | |
|---|---|
| [`docs/requirements.md`](docs/requirements.md) | V1 requirements |
| [`docs/domain-rules.md`](docs/domain-rules.md) | calculation and validation rules, with worked examples |
| [`docs/domain-model.md`](docs/domain-model.md) | entities and fields |
| [`docs/decisions.md`](docs/decisions.md) | decision log |
| [`docs/milestones.md`](docs/milestones.md) | implementation milestones and status |
| [`docs/ui/`](docs/ui/) | UI mockups and design tokens (reference for the frontend) |
| [`docs/import/`](docs/import/) | import specifications |
| [`docs/architecture/`](docs/architecture/) | architecture decisions (ADRs): stack, database, API, build |

## Developer quick start
Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 by itself), Node.js 24 LTS with npm, Docker.

```bash
# Backend: http://127.0.0.1:8000 — API docs at /api/docs, health at /api/health
cd backend
uv sync
PT_DATA_DIR=.data uv run python -m portfolio_tracker

# Frontend (second terminal): http://localhost:5173, /api is proxied to the backend
cd frontend
npm ci
npm run dev

# Checks
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd frontend && npm run lint && npm run typecheck && npm test && npm run build && npm run budget

# End-to-end tests in Chromium (needs uv too; download the browser once)
cd frontend
npx playwright install chromium
npm run e2e
```

`PT_DATA_DIR=.data` keeps the development database in `backend/.data/` (git-ignored) instead of `/data`. Over plain http on another computer, add `PT_COOKIE_SECURE=false` (see below).

**Invented development data.** To try the screens with some accounts and transactions, fill a new, empty data directory with made-up data (it refuses a directory that already has a user or an account), then start the app on it:

```bash
cd backend
PT_DATA_DIR=.data-demo uv run python scripts/dev_data.py --development
PT_DATA_DIR=.data-demo PT_COOKIE_SECURE=false PT_MARKET_DATA=false uv run python -m portfolio_tracker
```

Keep `PT_MARKET_DATA=false` on development data: market data is on by default, and a run would send the invented ISINs to the price providers and replace the invented exchange rates with real ones. The script prints this start command, with the logins it created. **Their passwords are public (they are in this repository): never use a development data directory for real data**, and never point `PT_DATA_DIR` of a real installation at one. Folders named `.data*` are for development only (git-ignored). The script refuses to run unless `PT_DATA_DIR` is set and the folder name starts with `.data`. The end-to-end tests start their own servers on such folders (`frontend/.data-e2e-empty/`, `frontend/.data-e2e-seeded/`, `frontend/.data-e2e-market/`), deleted at each run, on ports 8710, 8711 and 8712; the one on 8712 has market data on, answered by invented fakes from a test-only launcher in `backend/tests/` (never in the image), so it calls no provider either.

**Live check of market data (optional, for maintainers).** To see whether Yahoo, OpenFIGI and Frankfurter still answer as the app expects, run the whole market-data flow once on a few public securities (listed in the script), on a throwaway folder that must not exist yet. The check deletes the folder at the end, unless you add `--keep`:

```bash
cd backend
PT_LIVE_CHECK=1 PT_DATA_DIR=/tmp/.data-live-check uv run python scripts/live_check.py
```

It prints a report per security: the class, the chosen listing (home, secondary, or not judged where no public source names a home listing), the price status, the last close and the exchange rates. It makes at most 17 calls per security, plus 7 for a text search and 1 per currency other than euro, with at least 1 s between two Yahoo calls. It stops at the first 429 or 503 (exit code 3; 2 when it refuses to start, 1 when a step fails, with the error in the report). `--extra ISIN:CURRENCY` (at most 3) adds a public security for that run only. It never runs in CI, and it is never for real data (D-151).

## Running with Docker
```bash
docker build --platform linux/amd64 -t portfolio-tracker:dev .
mkdir -p data                     # owned by your user; the container runs as PUID:PGID (default 1000:1000)
docker compose -f docker-compose.example.yml up -d
curl http://localhost:8000/api/health
```
The app must be served at the root of its own host name or port (e.g. `https://portfolio.example.org/` or `http://server:8000/`): **hosting under a sub-path** such as `https://example.org/portfolio/` **is not supported** in V1.

One container, one mounted data volume (`/data`) holding the database (`/data/db`), backups and keys. Restoring the whole instance means starting a new container on the same volume. Never run two containers on the same data volume at the same time: both would write to one database and run their own background jobs. The app runs as a non-root user; choose the user with `PUID`/`PGID` (Compose) or `--user` (docker run) so it can write to the data folder. Platform in V1: linux/amd64.

Environment variables (so far): `PT_DATA_DIR` (default `/data`), `PT_HOST` (`0.0.0.0` in the image), `PT_PORT` (`8000`), `PT_LOG_LEVEL` (`INFO`), `PT_STATIC_DIR` (built frontend, set in the image), `PT_COOKIE_SECURE` (`true`), `PT_SESSION_IDLE_DAYS` (`7`), `PT_SESSION_ABSOLUTE_DAYS` (`30`), `PT_SECRET_KEY` (the key that encrypts provider credentials: base64 of 32 bytes, e.g. from `openssl rand -base64 32`; unset = a key generated at the first start in `/data/secrets/secret.key`, mode 0600). Backups never contain provider credentials or this key; keep a copy of the key yourself if you want stored credentials to survive the loss of the volume, otherwise enter them again. The full list and the update procedure are completed with the first release (M7).

Market data (M4; see `docs/decisions.md` D-116 to D-136): `PT_MARKET_DATA` (`off` = no outbound call at all and no market data job; default on), `PT_MARKET_DATA_TIME` (the daily run, `HH:MM` in UTC; default `22:00`). The engine settings below are **initial values**: they are copied into the database at the first start, and from then on the database wins (the admin area to change them comes in M6; a log line at startup says when the environment differs). `PT_PRICE_PROVIDERS` (engines for ETFs and stocks, in order, comma-separated; default `yahoo`; empty = manual prices only), `PT_BOND_PRICE_PROVIDERS` (engines for bonds, in order; default empty = manual prices for bonds), `PT_FX_PROVIDERS` (engines for exchange rates, in order; default `frankfurter`), `PT_CLASSIFY_PROVIDERS` (engines that tell whether an ISIN is a bond or an ETF/stock and find its exchange tickers, in order; default `openfigi,yahoo`), `PT_PRICE_EXCHANGES` (preferred exchanges, in order, comma-separated provider exchange codes such as `MIL` or `GER` (names such as `Milan` also work, codes are matched earlier), compared ignoring case; **empty by default**: each engine's main exchanges come first, those trading in the security's currency before the others (D-152), each group in the engine's order: for Yahoo Milan, Xetra, London, Amsterdam, Paris, SIX, Madrid, Brussels and Vienna, then the US exchanges. So a security held in EUR gets **Milan first** among the EUR exchanges, one in USD a US exchange, one in GBP London, one in CHF SIX; other venues such as international order books come last, D-125. If you hold your EUR securities on Xetra, set `PT_PRICE_EXCHANGES=GER`. An exchange set here ranks first even in another currency, but a listing is used only in the security's own currency), `PT_FX_URL` (Frankfurter base URL for ECB reference rates; default `https://api.frankfurter.dev`, or your self-hosted copy; read at every start). Engines added by a later version of the app start disabled.

**What leaves your server.** With market data on, the app sends each ISIN held in the instance to OpenFIGI (to classify it and find its exchange tickers) and to Yahoo Finance (to find listings, then the daily closes of each listing by the provider's own symbol), and asks Frankfurter for the exchange rates of the currencies in use (their codes and a date range). From the asset search, the text a user types to find a security (a ticker, a name or an ISIN) is sent to Yahoo Finance too. Requests identify themselves as `portfolio-tracker` (User-Agent). Nothing else is sent: no user, quantity, amount, account, or ticker or name a user entered. `PT_MARKET_DATA=off` stops every outbound call.

**HTTPS and the session cookie.** The login cookie is `Secure` by default: browsers send it only over HTTPS, or to `http://localhost`. If you open the app as `http://<server-ip>:8000` from another computer, login appears to succeed but you are sent back to the login page, and the log shows a warning that names `PT_COOKIE_SECURE`. Put the app behind a reverse proxy with TLS (recommended), or, only on a trusted home network, set `PT_COOKIE_SECURE=false`.

**Request size behind a reverse proxy.** The app refuses request bodies over 4 MiB, except the import uploads (`/api/v1/imports/preview` and `/api/v1/imports/confirm`), which carry a file of up to 10 MB as base64 and need about **15 MB**. Allow that much on `/api/v1/imports/*` in your proxy (nginx's default `client_max_body_size` of 1 MB would refuse imports larger than about 750 KB), e.g. `client_max_body_size 16m;` for that location.

**Forgotten administrator password.** There is no e-mail recovery. Run `docker exec -it portfolio-tracker python -m portfolio_tracker reset-password <username>`: it prints a temporary password once, ends that user's sessions and asks for a new password at the next login. Other users ask the administrator to reset theirs.

## Privacy
The application gives the administrator no way to read other users' portfolios, but data is **not** cryptographically isolated: anyone with access to the server, the `/data` volume or a backup file can read it. **Encrypt backups with a passphrase** (optional, strongly recommended) and protect the volume.

## Contributing
Documentation, code and commit messages are in English. **This repository is public: never commit real financial data or real broker exports (even anonymized)** — use the synthetic fixtures in `tests/fixtures/`. Real files for local checks go in `private/`, which is git-ignored.

## License
[GNU AGPL v3](LICENSE).
