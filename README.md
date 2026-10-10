# Portfolio Tracker

A self-hosted, open-source investment tracker for private investors. It answers one question: **"How are my investments performing?"** with a visual, jargon-free dashboard. Monitoring only — no trading.

> **Status: requirements complete, implementation not started.** There is no runnable code yet. See [`docs/milestones.md`](docs/milestones.md).

## Planned V1 highlights
- ETFs, stocks and bonds across several broker accounts, with an aggregated portfolio view
- Manual entry, and import from a Directa Excel export or the app's own CSV format (always with preview and confirmation)
- Gain/loss shown **gross and net of taxes**, unrealized and realized, plus dividends and coupons received
- Daily prices and FX stored locally; historical evolution charts and allocations
- Multi-user (families/households), optional two-factor authentication, Italian and English UI, light and dark themes
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
| [`docs/import/`](docs/import/) | import specifications |
| [`docs/architecture/`](docs/architecture/) | architecture decisions (ADRs) |

## Deployment (planned)
The application will run as a single container with one mounted data volume (`/data`) holding the database, backups and keys. Restoring means starting a new container on the same volume. A tested `docker-compose.yml`, the full list of environment variables and the update procedure will be added here when the first image is released. Platform in V1: linux/amd64.

## Privacy
The application gives the administrator no way to read other users' portfolios, but data is **not** cryptographically isolated: anyone with access to the server, the `/data` volume or a backup file can read it. **Encrypt backups with a passphrase** (optional, strongly recommended) and protect the volume.

## Contributing
Documentation, code and commit messages are in English. **This repository is public: never commit real financial data or real broker exports (even anonymized)** — use the synthetic fixtures in `tests/fixtures/`. Real files for local checks go in `private/`, which is git-ignored.

## License
[GNU AGPL v3](LICENSE).
