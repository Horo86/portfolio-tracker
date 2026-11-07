# Development

How to build, run and test Portfolio Tracker from source. For installing and running the app, see the [README](../README.md).

## Status
Milestones M0 to M6 are done; M7 is in progress: the 0.9.0 pre-release is published, and the rest for 1.0.0 follows D-168 to D-171. See [`milestones.md`](milestones.md).

## Documentation
| | |
|---|---|
| [`requirements.md`](requirements.md) | V1 requirements |
| [`domain-rules.md`](domain-rules.md) | calculation and validation rules, with worked examples |
| [`domain-model.md`](domain-model.md) | entities and fields |
| [`decisions.md`](decisions.md) | decision log |
| [`milestones.md`](milestones.md) | implementation milestones and status |
| [`roadmap-v2.md`](roadmap-v2.md) | what is not in V1 |
| [`ui/`](ui/) | UI mockups and design tokens (reference for the frontend) |
| [`import/`](import/) | import specifications |
| [`architecture/`](architecture/) | architecture decisions (ADRs): stack, database, API, build |
| [`adding-a-provider.md`](adding-a-provider.md) | how to add a market data engine |

## Quick start
Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 by itself), Node.js 24 LTS with npm, Docker.

```bash
# Backend: http://127.0.0.1:8000 — health at /api/health; API docs at /api/docs only with PT_API_DOCS=true
cd backend
uv sync
PT_DATA_DIR=.data uv run python -m portfolio_tracker

# Frontend (second terminal): http://localhost:5173, /api is proxied to the backend
cd frontend
npm ci
npm run dev
```

`PT_DATA_DIR=.data` keeps the development database in `backend/.data/` (git-ignored) instead of `/data`. Over plain http on another computer, add `PT_COOKIE_SECURE=false`.

## Checks
```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd frontend && npm run lint && npm run typecheck && npm test && npm run build && npm run budget && npm run licenses
```

`npm run build` also writes `dist/licenses/THIRD-PARTY.txt`, the third-party notices shipped in the image (D-167); `npm run budget` checks the size budget (D-138). The backend's licence check runs in CI (`.github/workflows/ci.yml`).

**End-to-end tests** in Chromium (they need uv too; download the browser once):

```bash
cd frontend
npx playwright install chromium
npm run e2e
# The acceptance path as one scenario, on a server of its own (D-159)
npm run e2e:walkthrough
```

They start their own servers on throwaway folders (`frontend/.data-e2e-empty/`, `.data-e2e-seeded/`, `.data-e2e-market/`), deleted at each run, on ports 8710, 8711 and 8712; the one on 8712 has market data on, answered by invented fakes from a test-only launcher in `backend/tests/` (never in the image), so it calls no provider. The walkthrough uses port 8713 and `frontend/.data-e2e-walkthrough/`.

## Invented development data
To try the screens with some accounts and transactions, fill a new, empty data directory with made-up data (it refuses a directory that already has a user or an account), then start the app on it:

```bash
cd backend
PT_DATA_DIR=.data-demo uv run python scripts/dev_data.py --development
PT_DATA_DIR=.data-demo PT_COOKIE_SECURE=false PT_MARKET_DATA=false uv run python -m portfolio_tracker
```

Keep `PT_MARKET_DATA=false` on development data: market data is on by default, and a run would send the invented ISINs to the price providers and replace the invented exchange rates with real ones. The script prints this start command, with the logins it created. **Their passwords are public (they are in this repository): never use a development data directory for real data**, and never point `PT_DATA_DIR` of a real installation at one. Folders named `.data*` are for development only (git-ignored); the script refuses to run unless `PT_DATA_DIR` is set and the folder name starts with `.data`.

**Reference size (optional, for maintainers).** `--reference` fills a new folder with the reference size of the requirements instead: 10 users (`ref01`…`ref10`), each holding 200 invented securities in three accounts, 5,000 transactions each over 20 years, and 20 years of daily closes (about 2.5 minutes, about 100 MB). Then `scripts/reference_timing.py` times the screens against the 2-second target (D-154): on a desktop computer the dashboard of all accounts takes about 2 s at this worst case, one account under 1 s. The same guards apply, and the same rule: start the app on it only with `PT_MARKET_DATA=false`.

```bash
cd backend
PT_DATA_DIR=.data-ref uv run python scripts/dev_data.py --development --reference
PT_DATA_DIR=.data-ref uv run python scripts/reference_timing.py
```

## README screenshots
`docs/images/` is made again with one command, never in CI. It needs the backend's environment (`uv sync`) and Playwright's Chromium (as for the end-to-end tests). It builds the frontend, fills a throwaway folder (`frontend/.data-screenshots/`) with the invented demo data, starts the app on it on port 8714 with market data off, enters the demo's manual prices again for yesterday so none shows as not updated, and takes the dashboard, the holdings list and the import preview of the invented sales fixture at 1440×900, in English. Then it stops its server and deletes the folder:

```bash
cd frontend
npm run screenshots
```

## Live check of market data
To see whether Yahoo, OpenFIGI and Frankfurter still answer as the app expects, run the whole market-data flow once on a few public securities (listed in the script), on a throwaway folder that must not exist yet. The check deletes the folder at the end, unless you add `--keep`:

```bash
cd backend
PT_LIVE_CHECK=1 PT_DATA_DIR=/tmp/.data-live-check uv run python scripts/live_check.py
```

It prints a report per security: the class, the chosen listing (home, secondary, or not judged where no public source names a home listing), the price status, the last close and the exchange rates. It makes at most 17 calls per security, plus 7 for a text search and 1 per currency other than euro, with at least 1 s between two Yahoo calls. It stops at the first 429 or 503 (exit code 3; 2 when it refuses to start, 1 when a step fails, with the error in the report). `--extra ISIN:CURRENCY` (at most 3) adds a public security for that run only. It never runs in CI, and it is never for real data (D-151).

## Building the image locally
```bash
docker build --platform linux/amd64 -t portfolio-tracker:dev .
```

A local build has no build date: health reports `build_date: null` and About says "Development build". To run it, use the README's compose file with `image: portfolio-tracker:dev`. CI builds the image on every pull request with the build arguments `VERSION`, `BUILD_DATE` and `BUILD_COMMIT` and checks it (ADR-0009).

## Versions and branches
Versions and branches follow D-170, and every migration the upgrade policy D-168.
- **Semantic versions `X.Y.Z`:** X for incompatible changes that may need manual steps (in the release notes), Y for new compatible features, Z for fixes and security fixes. A Z release carries no schema migration unless the fix truly needs one.
- **Trunk-based:** `main` stays green and releasable; each change goes on a short-lived branch named by type (`feat/`, `fix/`, `docs/`, `ci/`, `test/`, `chore/`) and is merged by pull request. The version lives in the tag `vX.Y.Z` and the version files, never in long-lived branches.
- **Maintenance branch `release/X.Y`:** cut from the tag `vX.Y.Z` only when X.Y needs a fix while `main` already holds unfinished work for the next version. The fix goes there and to `main`, and is tagged on that branch as the next `X.Y.Z`. *Until the release workflow accepts such a branch (an M7 pull request), tags go on `main` only.*
- **Pre-releases** `vX.Y.Z-rc.N` try a version on a real installation before the stable tag; they move no image alias.
- **Planning:** one GitHub milestone per planned version; the version bump is the last pull request of a version, and an rc has its own bump.
- **Migrations** (D-168): all pending revisions run in one transaction; work that needs the network only marks rows for the background loop; a migration that touches encrypted values uses the cipher of the start-up. Every released version has an upgrade fixture with invented data in `backend/tests/fixtures/upgrade/`, which must keep migrating to the head; after each release the next pull request adds that version's fixture.

## Cutting a release
Releases follow D-167 as amended by D-170; the workflow is `.github/workflows/release.yml`.

1. **Bump the version** in a pull request: `backend/pyproject.toml` and its entry in `backend/uv.lock` (`uv lock`), `frontend/package.json` and both `version` fields of `frontend/package-lock.json`, and the image tag in `docker-compose.example.yml` (a test fails if the README's copy or the compose tag disagree with `pyproject.toml`). A version with a suffix is written `1.0.0-rc.1` in all three files (Python reports it as `1.0.0rc1`); only `-alpha.N`, `-beta.N` and `-rc.N` are accepted.
2. **Tag** the merged commit on `main`, only after the maintainer's explicit "go": `git tag -a v0.9.0 -m "Preview release 0.9.0"`, then `git push origin v0.9.0`. A tag is never moved or deleted: a mistake is fixed in the next patch version.
3. **The workflow runs** on that tag only (never on a pull request, a branch or a fork):
   - `verify`: the tag must be `vX.Y.Z` or `vX.Y.Z-(alpha|beta|rc).N`, equal to the version in `pyproject.toml`, `package.json` and `package-lock.json`, and the tagged commit must be on `main`;
   - `ci`: all of `ci.yml` on the tagged commit (end-to-end tests, the walkthrough and the image checks included);
   - `publish`, only when both are green: builds one `linux/amd64` image with the build date (the commit time) and commit, smoke-tests it and checks its labels, refuses to go on if `X.Y.Z` is already in the registry (or if the registry cannot say), pushes `ghcr.io/horo86/portfolio-tracker:X.Y.Z`, and `:X.Y` only when this is the highest `vX.Y.*` tag (a suffixed version only as itself; never `latest` before 1.0.0; from 1.0.0 also `X` and `latest`, each only for the highest plain version in its scope, D-170, once the workflow change lands), with the workflow's own `GITHUB_TOKEN`; then it pulls the image back by digest and checks its health again.
   - One release runs at a time: a second tag waits for the first run to end. GitHub keeps only one run waiting, so a third tag pushed meanwhile cancels the waiting one: **push one tag at a time**, and rerun a cancelled release with "Re-run all jobs". Whether `X.Y` moves is decided just before the push, so a re-run of an older release never moves it back.
   - A published version is never pushed again, so re-running a release that already pushed fails; fix a broken release with the next patch version. If `X.Y.Z` was pushed but the `X.Y` push failed, `X.Y` catches up with the next patch (no manual retag with a personal token).
4. **On GitHub,** the maintainer creates the release from the tag (marked pre-release for a preview version), and after the first push makes the package public (Packages → portfolio-tracker → Package settings).
5. **After a stable release** (not an rc), in the next pull request (D-168 (7)):
   - its upgrade fixture, made on the tag's own commit: `cd backend && uv run python scripts/make_upgrade_fixture.py --development --version X.Y.Z --work .data-fixture` (invented data only; it writes `tests/fixtures/upgrade/X.Y.Z.sqlite`, its expected figures and its `index.json` entry). A release that changed neither the schema nor the shape of the data may instead add `{"version": "X.Y.Z", "same_as": "<earlier version>"}` to `index.json` by hand;
   - `.github/upgrade-from.txt` names the new image pinned by its digest (the release run prints `pushed …@sha256:…`), so CI upgrades a data folder of that image from then on.

   The release workflow refuses a stable tag while an earlier stable release has no fixture (`.github/scripts/upgrade-fixture-gate.py`).

Every action in the workflows is pinned to a full commit SHA, with its version in a comment.

## Contributing
Documentation, code and commit messages are in English; the UI texts live in `frontend/src/locales/` (Italian and English). **This repository is public: never commit real financial data or real broker exports (even anonymized)** — use the synthetic fixtures in `tests/fixtures/`. Real files for local checks go in `private/`, which is git-ignored.
