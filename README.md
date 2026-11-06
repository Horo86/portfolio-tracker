# <img src="docs/ui/icon.svg" alt="Portfolio Tracker icon: the letters P and T joined, with a small rising line" width="40" height="40"> Portfolio Tracker

A self-hosted, open-source investment tracker for private investors. It answers one question: **"How are my investments performing?"** with a visual, jargon-free dashboard. ETFs, stocks and bonds across several broker accounts, in one container on your own server. Monitoring only — no trading.

![Dashboard: total value, invested capital and net gain cards, the table of where the gain comes from, and the allocation by type and by currency](docs/images/dashboard.png)

> [!WARNING]
> **0.9.0 is a preview, for testing.** It works, but it is not 1.0.0 yet, and some things are missing:
> - **There is no backup or restore in the app yet.** Before every upgrade, stop the container and copy the whole `data` folder (the database and `secrets/`): see [Your data](#your-data).
> - **There is no two-factor authentication.** If the app is reachable from the Internet, put it behind a VPN or a reverse proxy with its own authentication: see [Security](#security).

## Features
- Several broker accounts, each in its own currency, and the whole portfolio in your reporting currency.
- Transactions of seven types (buy, sell, dividend, coupon, deposit, withdrawal, stock split), entered by hand with a live summary, edited one by one or in bulk, and exported as CSV.
- Import of **Directa** Excel exports and of the app's own CSV, always with a preview to check and tick before anything is saved.
- Holdings with average cost, last price, value and gain **gross and net of taxes**, unrealized and realized, plus dividends and coupons received; securities found by ISIN or name, or created by hand; your own prices where no automatic price exists (bonds, for now).
- A dashboard with the figures that matter, the allocation by security, type and currency, the best and worst holdings, and the income by month and year.
- Daily closes and ECB exchange rates downloaded in the background, or none at all (`PT_MARKET_DATA=false`).
- Several users on one installation (a family, a household); Italian and English; four light color themes; a desktop layout.

![Holdings list: five invented securities in two accounts with quantity, average cost, last price, value and gross and net gain](docs/images/holdings.png)

![Import preview of an invented Directa file: 8 transactions to import, and a sale whose capital gains tax was linked by date, to tick if right](docs/images/import-preview.png)

*The screenshots use invented demo data.*

**Coming in 1.0.0** (what we intend, not a promise of a date):
- a whole-installation backup from the app, with optional (recommended) encryption, and restore from the first-start screen;
- the pre-migration copies named and tidied up automatically;
- the list of third-party licences on the About page;
- the documentation completed for the release.

Later versions bring the history charts, two-factor authentication, settings screens for security and market data, a dark theme and a mobile layout, among others: see [`docs/roadmap-v2.md`](docs/roadmap-v2.md).

## Quick start
You need Docker with Compose on a **linux/amd64** computer (a PC, most NAS units with an Intel or AMD processor).

1. Make a folder for the app, and in it a `data` folder owned by your user:
   ```bash
   mkdir portfolio-tracker && cd portfolio-tracker
   mkdir data
   ```
2. Save this file there as `docker-compose.yml` (it is [`docker-compose.example.yml`](docker-compose.example.yml) in this repository). If `id` does not show uid and gid 1000, set `PUID` and `PGID` (e.g. in a `.env` file next to it):

```yaml
# Portfolio Tracker: save this file as docker-compose.yml in a folder of its own.
# Before the first start, in that folder: mkdir data   (owned by the user that runs the app)
# PUID/PGID default to 1000 (the first user on most Linux systems); check yours with `id`.
services:
  portfolio-tracker:
    image: ghcr.io/horo86/portfolio-tracker:0.9.0
    platform: linux/amd64
    container_name: portfolio-tracker
    user: "${PUID:-1000}:${PGID:-1000}"
    ports:
      # This computer only. To open it from other devices on your network, use the line below
      # instead, and read "HTTPS and the session cookie" in the README first.
      - "127.0.0.1:8000:8000"
      # - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      PT_LOG_LEVEL: INFO
      # The session cookie is Secure: serve the app over HTTPS (reverse proxy) or open it
      # on localhost. Only on a trusted LAN over plain http: PT_COOKIE_SECURE: "false"
      # Key for provider credentials; without it one is generated in data/secrets/secret.key.
      # PT_SECRET_KEY: "<output of: openssl rand -base64 32>"
      # Behind a reverse proxy only: its address or network, so the login throttle and the
      # audit log see the real client (README, Security). Empty = no proxy. Never 0.0.0.0/0.
      # PT_TRUSTED_PROXIES: "172.18.0.2"
      # Market data: "false" makes no outbound call at all; the daily run is at 22:00 UTC.
      # PT_MARKET_DATA: "false"
      # PT_MARKET_DATA_TIME: "22:00"
    healthcheck:
      # The same check as the image's, written out here so it can be seen and tuned.
      test: ["CMD", "python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PT_PORT', '8000') + '/api/health', timeout=4)"]
      interval: 30s
      timeout: 5s
      start_period: 15s
      retries: 3
    restart: unless-stopped
    # Gives a market data download in progress time to end on `docker compose stop` (no new
    # provider call starts once stopping; a very slow answer is cut and retried next run).
    stop_grace_period: 30s
    # One container per data folder: never start a second one on the same ./data.
```

3. Start it, and check that it is healthy:
   ```bash
   docker compose up -d
   docker compose ps
   ```
4. Open http://localhost:8000 on the same computer.

**The session cookie is `Secure`:** browsers send it only over HTTPS or to `localhost`. To use the app from other devices, read [HTTPS and the session cookie](#https-and-the-session-cookie) first, or login will seem to work and then send you back to the login page.

## First steps
1. **Welcome:** choose "Start from scratch" and create the administrator (a password of at least 12 characters). "Restore from a backup" is not available yet.
2. **Accounts:** create an account for each broker account, in its currency.
3. **Holdings:** "Add a security" finds one by ISIN or name, or creates it by hand; "Add holdings I already own" enters what you hold today in one go. Or use **Import** for a Directa Excel export or the app's CSV.
4. **Prices:** with market data on, closes and exchange rates arrive at the latest with the next daily run, at 22:00 UTC (`PT_MARKET_DATA_TIME`). Until a security has a price it is valued at cost. Bonds have no automatic price yet: enter your own on the holding's page.
5. **Other users:** an administrator adds them in **Administration** → Users (create, reset a password, disable and enable). Each user sees only their own portfolio.

## Your data
Everything lives in the `data` folder next to the compose file (`/data` in the container):

| | |
|---|---|
| `db/app.sqlite` | the database: users, accounts, transactions, prices (with `app.sqlite-wal` and `app.sqlite-shm` while the app runs) |
| `secrets/secret.key` | the key that encrypts stored provider credentials, made at the first start unless `PT_SECRET_KEY` is set (then keep that key yourself: without it, stored credentials must be entered again) |
| `backups/pre-migration/` | a copy of the database made automatically before each upgrade that changes it |

**To make a copy,** stop the app first, so the database is not written while you copy it, then copy the whole folder and start again:

```bash
docker compose stop
cp -a data data-copy-$(date +%Y%m%d)
docker compose start
```

The copies are not encrypted: they hold all your financial data. Keep them somewhere safe, such as an encrypted disk.

## Updating and rolling back
Choose a version tag: `0.9.0` stays exactly that version, while `0.9` also follows its fixes (0.9.1, …). There is no `latest` tag before 1.0.0.

**To update:**
1. `docker compose stop`, then copy the `data` folder as above.
2. Set the new version in `image:` in `docker-compose.yml`.
3. `docker compose pull`, then `docker compose up -d`.

If the new version changes the database, it first writes its own copy of the database to `data/backups/pre-migration/`, then migrates; `docker compose logs` says "database migrated", or "database schema up to date" when nothing changed. That copy holds the database only, not `secrets/`: your own copy of the whole folder is still the one to keep.

**To roll back:** stop the app, put the copy of the `data` folder made before the upgrade back in its place (keep the newer folder aside), set the old version in `image:`, and `docker compose up -d`.

An older version **refuses a database written by a newer one**: it stops with a message naming the database's version, without changing anything, and exits with code 1. With `restart: unless-stopped`, Docker keeps restarting it and the log repeats the message; those restarts create and change nothing. The fix: `docker compose stop`, then either start the newer image again or put back the copy of the `data` folder.

## Security
- **No two-factor authentication yet** (see the warning at the top): behind a VPN or an authenticating reverse proxy if reachable from the Internet, otherwise off the Internet.
- **TLS and HSTS are the reverse proxy's job.** The app sets its own security headers on every response (a strict Content Security Policy, no framing, `nosniff`, `Referrer-Policy` and `Cross-Origin-Opener-Policy`) and secure cookies; it sends no HSTS and never upgrades http to https, so plain http on localhost keeps working.
- **Serve it at the root** of its own host name or port (`https://portfolio.example.org/` or `http://server:8000/`): a sub-path such as `https://example.org/portfolio/` is not supported.
- **One container per data folder.** Two containers on the same `data` would both write to one database and run their own background jobs.
- **Sessions.** A password change gives your current session a new token and ends your other sessions; logging in ends the session the browser was carrying, also when someone else logs in on the same browser.
- **An administrator password reset lets the administrator log in as that user.** It is recorded in the audit log, and the user is told at the next login.

### HTTPS and the session cookie
The login cookie is `Secure` by default: browsers send it only over HTTPS, or to `http://localhost`. If you open the app as `http://<server-ip>:8000` from another computer, login appears to succeed but you are sent back to the login page, and the log shows a warning that names `PT_COOKIE_SECURE`. Put the app behind a reverse proxy with TLS (recommended), or, only on a trusted home network, set `PT_COOKIE_SECURE: "false"` and publish the port to your network (the commented `ports:` line).

### Behind a reverse proxy
- **`PT_TRUSTED_PROXIES`:** comma-separated addresses or networks (CIDR) of your reverse proxies. With no reverse proxy, leave it empty: the client is the direct peer and `X-Forwarded-For` / `X-Forwarded-Proto` are ignored. Behind a proxy, set it to the proxy's address (e.g. `172.18.0.2`, or its Docker network `172.18.0.0/16`): only from it are the client address (the rightmost `X-Forwarded-For` hop that is not a listed proxy) and the scheme believed, for the login throttle, the audit log, the logs and the plain-http warning. Never `0.0.0.0/0` (refused at start: anyone could choose their own address); an invalid entry stops the start with a message.
- **Keep `PT_COOKIE_SECURE` true** behind a proxy serving HTTPS: the cookie's `Secure` flag never follows `X-Forwarded-Proto`.
- **Request size.** The app refuses request bodies over 4 MiB, except the import uploads (`/api/v1/imports/preview` and `/api/v1/imports/confirm`), which carry a file of up to 10 MB as base64 and need about **15 MB**. Allow that much on `/api/v1/imports/*` in your proxy (nginx's default `client_max_body_size` of 1 MB would refuse imports larger than about 750 KB), e.g. `client_max_body_size 16m;` for that location.

### What leaves your server
With market data on, the app sends each ISIN held in the installation to OpenFIGI (to classify it and find its exchange tickers) and to Yahoo Finance (to find listings, then the daily closes of each listing by the provider's own symbol), and asks Frankfurter for the exchange rates of the currencies in use (their codes and a date range). From the asset search, the text a user types to find a security (a ticker, a name or an ISIN) is sent to Yahoo Finance too. Requests identify themselves as `portfolio-tracker` (User-Agent). Nothing else is sent: no user, quantity, amount, account, or ticker or name a user entered. `PT_MARKET_DATA=false` stops every outbound call. There is no telemetry.

### Forgotten administrator password
There is no e-mail recovery. Run `docker exec -it portfolio-tracker python -m portfolio_tracker reset-password <username>`: it prints a temporary password once, ends that user's sessions and asks for a new password at the next login, where the user is told that the administrator reset it. Other users ask the administrator to reset theirs.

### Privacy
The app gives the administrator no way to read other users' portfolios, but the data is **not** cryptographically isolated: anyone with access to the server, the `data` folder or a copy of it can read it. Protect the folder and your copies.

## Configuration
All settings are environment variables (the `environment:` section of the compose file).

| Variable | Default | Meaning |
|---|---|---|
| `PT_DATA_DIR` | `/data` | the data folder in the container |
| `PT_HOST` | set by the image (`0.0.0.0`) | the address the server listens on |
| `PT_PORT` | `8000` | the port the server listens on |
| `PT_STATIC_DIR` | set by the image | the built web app |
| `PT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`; logs are JSON lines on standard output |
| `PT_COOKIE_SECURE` | `true` | the session cookie's `Secure` flag; `false` only on a trusted LAN over plain http |
| `PT_TRUSTED_PROXIES` | empty | your reverse proxies' addresses or networks ([Behind a reverse proxy](#behind-a-reverse-proxy)) |
| `PT_SESSION_IDLE_DAYS` | `7` | a session ends after this many days without use |
| `PT_SESSION_ABSOLUTE_DAYS` | `30` | a session ends this many days after login |
| `PT_SECRET_KEY` | unset | the key that encrypts provider credentials, base64 of 32 bytes (`openssl rand -base64 32`); unset: `data/secrets/secret.key`, made at the first start |
| `PT_API_DOCS` | `false` | `true` serves the API docs at `/api/docs`, for development only (the page loads a script from jsDelivr): never on an Internet-facing install |
| `PT_MARKET_DATA` | `true` | `false`: no outbound call at all and no market data job |
| `PT_MARKET_DATA_TIME` | `22:00` | the daily market data run, `HH:MM` in UTC |
| `PT_PRICE_PROVIDERS` | `yahoo` | engines for ETF and stock prices, in order; empty: your own prices only |
| `PT_BOND_PRICE_PROVIDERS` | empty | engines for bond prices; empty: your own prices for bonds |
| `PT_FX_PROVIDERS` | `frankfurter` | engines for exchange rates |
| `PT_CLASSIFY_PROVIDERS` | `openfigi,yahoo` | engines that tell a bond from an ETF or stock and find its exchange tickers |
| `PT_PRICE_EXCHANGES` | empty | preferred exchanges, in order, e.g. `GER` for Xetra (below) |
| `PT_FX_URL` | `https://api.frankfurter.dev` | the Frankfurter address for ECB rates, or your own copy |
| `PT_BUILD_DATE` | set by the image | the build's date, shown on About |
| `PT_BUILD_COMMIT` | set by the image | the build's commit |

**The engine settings are initial values** (`PT_PRICE_PROVIDERS`, `PT_BOND_PRICE_PROVIDERS`, `PT_FX_PROVIDERS`, `PT_CLASSIFY_PROVIDERS`, `PT_PRICE_EXCHANGES`): they are copied into the database at the first start, and from then on the database wins. To apply new values, change the variables, restart, and run `docker exec -it portfolio-tracker python -m portfolio_tracker reset-market-data-config`: it shows the stored and the new values and asks before writing. A log line at startup says when the environment differs. Engines added by a later version start disabled.

**Which exchange gives the price.** With `PT_PRICE_EXCHANGES` empty, each engine's main exchanges come first, those trading in the security's currency before the others; for Yahoo: Milan, Xetra, London, Amsterdam, Paris, SIX, Madrid, Brussels and Vienna, then the US exchanges. So a security held in EUR gets Milan first, one in USD a US exchange, one in GBP London, one in CHF SIX; other venues come last. If you hold your EUR securities on Xetra, set `PT_PRICE_EXCHANGES: "GER"`. Codes are the provider's (`MIL`, `GER`, …; names such as `Milan` also work), compared ignoring case. An exchange set here ranks first even in another currency, but a listing is used only in the security's own currency.

## Licence
Portfolio Tracker is free software under the [GNU Affero General Public License v3](LICENSE) (AGPL-3.0-only); its source code is at https://github.com/Horo86/portfolio-tracker. If you offer it to others over a network, you must make its source code available to them.

In the image, the licence is `/app/LICENSE`. The web app's third-party packages, with their licence texts, are listed in `/app/static/licenses/THIRD-PARTY.txt`, also served at `/licenses/THIRD-PARTY.txt` (the Manrope font's licence at `/licenses/Manrope-OFL.txt`). The server's Python packages keep their licence files in their `*.dist-info` folders under `/app/.venv`.

## Development
Building from source, the tests, the development data and the documentation index are in [`docs/development.md`](docs/development.md). **This repository is public: never commit real financial data or real broker exports**, even anonymized.
