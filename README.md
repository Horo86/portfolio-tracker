# <img src="docs/ui/icon.svg" alt="Portfolio Tracker icon: the letters P and T joined, with a small rising line" width="40" height="40"> Portfolio Tracker

A self-hosted, open-source investment tracker for private investors. It answers one question: **"How are my investments performing?"** with a visual, jargon-free dashboard. Monitoring only — no trading.

> **Status: early development, not usable yet.** Milestones M0 (architecture and skeleton), M1 (calculation engine) and M2 (database, API and basic authentication) are done: with the API (`/api/docs`) you can set up the instance, log in, manage users, accounts, assets, manual prices and transactions, and read account and portfolio summaries. There is no user interface, import or automatic price/FX download yet. The UI mockups (M0.5) are ready in [`docs/ui/`](docs/ui/). Next: M3 (import). See [`docs/milestones.md`](docs/milestones.md).

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
cd frontend && npm run lint && npm run typecheck && npm test && npm run build
```

`PT_DATA_DIR=.data` keeps the development database in `backend/.data/` (git-ignored) instead of `/data`.

## Running with Docker
```bash
docker build --platform linux/amd64 -t portfolio-tracker:dev .
mkdir -p data                     # owned by your user; the container runs as PUID:PGID (default 1000:1000)
docker compose -f docker-compose.example.yml up -d
curl http://localhost:8000/api/health
```
One container, one mounted data volume (`/data`) holding the database (`/data/db`), backups and keys. Restoring the whole instance means starting a new container on the same volume. The app runs as a non-root user; choose the user with `PUID`/`PGID` (Compose) or `--user` (docker run) so it can write to the data folder. Platform in V1: linux/amd64.

Environment variables (so far): `PT_DATA_DIR` (default `/data`), `PT_HOST` (`0.0.0.0` in the image), `PT_PORT` (`8000`), `PT_LOG_LEVEL` (`INFO`), `PT_STATIC_DIR` (built frontend, set in the image), `PT_COOKIE_SECURE` (`true`), `PT_SESSION_IDLE_DAYS` (`7`), `PT_SESSION_ABSOLUTE_DAYS` (`30`). The full list and the update procedure are completed with the first release (M7).

**HTTPS and the session cookie.** The login cookie is `Secure` by default: browsers send it only over HTTPS, or to `http://localhost`. If you open the app as `http://<server-ip>:8000` from another computer, login appears to succeed but you are sent back to the login page, and the log shows a warning that names `PT_COOKIE_SECURE`. Put the app behind a reverse proxy with TLS (recommended), or, only on a trusted home network, set `PT_COOKIE_SECURE=false`.

**Forgotten administrator password.** There is no e-mail recovery. Run `docker exec -it portfolio-tracker python -m portfolio_tracker reset-password <username>`: it prints a temporary password once, ends that user's sessions and asks for a new password at the next login. Other users ask the administrator to reset theirs.

## Privacy
The application gives the administrator no way to read other users' portfolios, but data is **not** cryptographically isolated: anyone with access to the server, the `/data` volume or a backup file can read it. **Encrypt backups with a passphrase** (optional, strongly recommended) and protect the volume.

## Contributing
Documentation, code and commit messages are in English. **This repository is public: never commit real financial data or real broker exports (even anonymized)** — use the synthetic fixtures in `tests/fixtures/`. Real files for local checks go in `private/`, which is git-ignored.

## License
[GNU AGPL v3](LICENSE).
