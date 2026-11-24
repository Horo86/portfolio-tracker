# Development

How to build, run and test Portfolio Tracker from source. For installing and running the app, see the [README](../README.md).

## Status
Milestones M0 to M7 are done: **1.1.0 is released** (tag `v1.1.0`, image published; the latest stable release, after 1.0.2), after the 0.9.0 pre-release and 1.0.0-rc.1 to rc.3 (D-173: user details, one baseline migration, release notes in the app; D-174: TOTP 2FA, mandatory for administrators; D-175: small fixes; D-168 to D-171 for 1.0.0). Its upgrade fixtures are in `backend/tests/fixtures/upgrade/` and CI upgrades a data folder of the 1.1.0 image (D-168 (7); 1.0.1 and 1.0.2 have the same data as 1.0.0, so `index.json` points them to the 1.0.0 fixture; 1.1.0 has its own, revision `1002`). See [`milestones.md`](milestones.md). In 1.1.0 (released), the phone and tablet layout is complete (D-189 to D-195; `docs/ui/README.md`, "Small screens").

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

`npm run build` also writes `dist/licenses/THIRD-PARTY.txt`, the web app's third-party notices, and `dist/licenses/frontend.json`, its list for About (D-167); `npm run budget` checks the size budget (D-138). The backend's licence check runs in CI (`.github/workflows/ci.yml`).

**Dependency audits** (CI, on every pull request, on `main` and before a release; they install nothing):
- **Python:** `uv export --frozen --all-groups --no-emit-project` writes every locked package (runtime and development) with its hashes; `uv run pip-audit --strict --disable-pip --require-hashes -r <that file>` (pip-audit is a pinned development dependency) asks PyPI's vulnerability data about each name and version. Any known vulnerability is a finding.
- **Web app:** `npm audit --omit=dev --audit-level=high` sends the lockfile's shipped packages (names and versions) to `registry.npmjs.org`; high or critical is a finding. A second step, `npm audit --audit-level=critical`, covers the development tools too and is report-only: it prints and never fails the job, and what it finds is reviewed by hand.
- **A finding warns on pull requests and on `main`, and fails a release.** A new advisory on a dependency arrives without any change of ours, so on a pull request and on `main` the two audits above still run and print everything, but a finding only puts a warning on the run ("Dependency audit (Python)" or "Dependency audit (web app)", with a note in the run's summary) and the job stays green. In a release it fails: `release.yml` calls `ci.yml` with `audit_blocks: true`, so nothing is published until the package is updated or the finding is an exception below. The choice is in one place, `.github/scripts/dependency-audit.sh` (`AUDIT_BLOCKS`; anything but `false` blocks). Look at the warnings of the last run on `main` before tagging a version: a red release run after the tag costs a new tag.
- **What happens to a finding:** a shipped package with a fixed version gets a `fix/` pull request that updates it; a finding with no fix, or in code the app does not use, becomes an exception below, with `--ignore-vuln <ID>` in the CI command; development-only findings are reviewed by the maintainer.

**Audit exceptions** (ID, package, reason, when to check again): none.

**Third-party notices of the server** (D-167): the image build runs `backend/scripts/third_party_notices.py` on the virtual environment without development packages; it writes `BACKEND-THIRD-PARTY.txt` (each package's licence and the text of its licence files) and `backend.json` (name, version, licence) next to the web app's, in `/app/static/licenses/`, served at `/licenses/` and listed on About (a development run has no `backend.json`, and About says the list comes with the built app). The licence is read from `License-Expression`, then `License`, then the `License ::` classifiers; a licence file alone also counts; the build fails when a package gives none of these. Try it locally with `uv run python scripts/third_party_notices.py --output <folder>` (it then lists the development packages too). A missing file under `/licenses/` is a 404, never the app's page.

**End-to-end tests** in Chromium (they need uv too; download the browser once):

```bash
cd frontend
npx playwright install chromium
npm run e2e
# The acceptance path as one scenario, on a server of its own (D-159)
npm run e2e:walkthrough
```

They start their own servers on throwaway folders (`frontend/.data-e2e-empty/`, `.data-e2e-seeded/`, `.data-e2e-market/`, `.data-e2e-two-factor/`), deleted at each run, on ports 8710, 8711, 8712 and 8715; the one on 8712 has market data on, answered by invented fakes from a test-only launcher in `backend/tests/` (never in the image), so it calls no provider. Phones and tablets (D-189 to D-195): `e2e/phone.spec.ts` at 390×844 and `e2e/tablet.spec.ts` at 820×1180, `e2e/reflow.spec.ts` at 320×640 (every page: no sideways scroll, nothing wider than the window, the bottom bar at 64 px), and the axe walk of `e2e/a11y.spec.ts` also at 390 px; all in Chromium with `test.use`, no Playwright project of their own. One file or one test: `npm run e2e -- e2e/reflow.spec.ts` or `npm run e2e -- e2e/phone.spec.ts -g "accounts"`. These run with `PT_REQUIRE_2FA=off`, except the one on 8715, empty, where administrators must enrol in two-factor authentication (`e2e/two-factor.spec.ts`, D-174); its TOTP codes come from `e2e/helpers.ts` (Node's HMAC, increasing steps so that no test waits for the next 30-second step). The walkthrough uses port 8713 and `frontend/.data-e2e-walkthrough/`, with the obligation on: its administrator enrols in part A.

## Invented development data
To try the screens with some accounts and transactions, fill a new, empty data directory with made-up data (it refuses a directory that already has a user or an account), then start the app on it:

```bash
cd backend
PT_DATA_DIR=.data-demo uv run python scripts/dev_data.py --development
PT_DATA_DIR=.data-demo PT_COOKIE_SECURE=false PT_MARKET_DATA=false PT_REQUIRE_2FA=off uv run python -m portfolio_tracker
```

`PT_REQUIRE_2FA=off` lets the invented administrator in without enrolling in two-factor authentication (D-174): its password is public, so this is for development data only. Keep `PT_MARKET_DATA=false` on development data: market data is on by default, and a run would send the invented ISINs to the price providers and replace the invented exchange rates with real ones. The script prints this start command, with the logins it created. **Their passwords are public (they are in this repository): never use a development data directory for real data**, and never point `PT_DATA_DIR` of a real installation at one. Folders named `.data*` are for development only (git-ignored); the script refuses to run unless `PT_DATA_DIR` is set and the folder name starts with `.data`.

**Reference size (optional, for maintainers).** `--reference` fills a new folder with the reference size of the requirements instead: 10 users (`ref01`…`ref10`), each holding 200 invented securities in three accounts, 5,000 transactions each over 20 years, and 20 years of daily closes (about 2.5 minutes, about 100 MB). Then `scripts/reference_timing.py` times the screens against the 2-second target (D-154): on a desktop computer the dashboard of all accounts takes about 2 s at this worst case, one account under 1 s. The same guards apply, and the same rule: start the app on it only with `PT_MARKET_DATA=false`.

```bash
cd backend
PT_DATA_DIR=.data-ref uv run python scripts/dev_data.py --development --reference
PT_DATA_DIR=.data-ref uv run python scripts/reference_timing.py
```

## README screenshots
`docs/images/` is made again with one command, never in CI. It needs the backend's environment (`uv sync`) and Playwright's Chromium (as for the end-to-end tests). It builds the frontend, fills a throwaway folder (`frontend/.data-screenshots/`) with the invented demo data, starts the app on it on port 8714 with market data off, enters the demo's manual prices again for yesterday so none shows as not updated, and takes the dashboard and the holdings list at 1440×900 and three phone shots at 390×844 (device scale 2, touch: the dashboard's cards in light, the allocation donut in dark by the browser's dark colour scheme, the holdings as cards), all in English. Then it stops its server and deletes the folder:

```bash
cd frontend
npm run screenshots
```

## Review screenshots of the phone and tablet layout
For reviewing the layout of D-189 by eye, never in CI and never committed. The same setup as the README screenshots; it fills `frontend/.data-review/` with the invented demo data, starts the app on port 8716 with market data off, and writes every main page (and Administration as the demo administrator, and the open "Altro" menu) at 390×844 and 820×1180, light Moderno and dark, in Italian, plus the dashboard in dark at 844×390, to `frontend/.review-screenshots/` (ignored by git). The screens without a session (D-195) come from fresh invented users: the login, the forced password change and the login's code step on the demo copy, and the first run and the forced enrolment on a second throwaway server, empty, on port 8718 in `frontend/.data-review-empty/`; the search and the price check from the test-only launcher of fake providers on port 8717. Then it stops its servers and deletes their data folders, also on a crash or an interruption. `--only=transactions`, `--only=import`, `--only=add` and `--only=rest` (accounts, settings, About, not found and the screens without a session) take one part, for a quicker look (`npm run screenshots:review -- --only=rest`):

```bash
cd frontend
npm run screenshots:review
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
- **Maintenance branch `release/X.Y`:** cut from the tag `vX.Y.Z` only when X.Y needs a fix while `main` already holds unfinished work for the next version. The fix goes there and to `main`, and is tagged on that branch as the next `X.Y.Z`; the release workflow accepts a tag on `release/X.Y` only with the same X.Y (steps in "Cutting a release").
- **Pre-releases** `vX.Y.Z-rc.N` try a version on a real installation before the stable tag; they move no image alias.
- **Planning:** one GitHub milestone per planned version; the version bump is the last pull request of a version, and an rc has its own bump.
- **Release notes** (D-173): every pull request that changes something a user sees or must know adds a line under "Unreleased" in `CHANGELOG.md`, in plain language, under `### Added`, `Changed`, `Fixed`, `Removed` or `Security`, one line per item. The version bump moves "Unreleased" under the new version's number and leaves an empty "Unreleased". The build turns the file into the release notes on About (`frontend/scripts/release-notes.mjs`), `backend/tests/test_changelog.py` checks its format and fails when the current version has no section, and the release workflow writes that section out for the GitHub release (`.github/scripts/release-notes.py`).
- **Migrations** (D-168): all pending revisions run in one transaction; work that needs the network only marks rows for the background loop; a migration that touches encrypted values uses the cipher of the start-up. Every released version has an upgrade fixture with invented data in `backend/tests/fixtures/upgrade/`, which must keep migrating to the head; after each release the next pull request adds that version's fixture. **From 1.0.0-rc.2** (D-173) the schema starts from one baseline revision, `1000`, never edited after that tag; data of the previews 0.9.0 and 1.0.0-rc.1 is refused untouched, never upgraded, and the fixture index lists them as unsupported; the first fixtures are those of 1.0.0-rc.2 and 1.0.0.

## Cutting a release
Releases follow D-167 as amended by D-170; the workflow is `.github/workflows/release.yml`.

1. **Bump the version** in a pull request: `backend/pyproject.toml` and its entry in `backend/uv.lock` (`uv lock`), `frontend/package.json` and both `version` fields of `frontend/package-lock.json`, the image tag in `docker-compose.example.yml`, the version tag in the README's download address of that file (`…/v<version>/docker-compose.example.yml`), and `CHANGELOG.md` ("Unreleased" becomes `## <version>`, a new empty "Unreleased" above it; `backend/tests/test_changelog.py` and the build fail without that section); `backend/tests/test_readme_compose.py` fails if the README's address, the compose image or the lines the README asks to check disagree with `pyproject.toml` or with the compose file. An rc bump points them to the rc, so the rc is tried by following the README. A version with a suffix is written `1.0.0-rc.1` in all three files (Python reports it as `1.0.0rc1`); only `-alpha.N`, `-beta.N` and `-rc.N` are accepted.
2. **Tag** the merged commit on `main`, only after the maintainer's explicit "go": `git tag -a v0.9.0 -m "Preview release 0.9.0"`, then `git push origin v0.9.0`. A tag is never moved or deleted: a mistake is fixed in the next patch version.
   - **A fix for an older line** while `main` holds unfinished work (D-170 (3)): cut the branch once from that line's latest tag, `git switch -c release/1.0 v1.0.0` and `git push origin release/1.0`; each fix is a `fix/…` branch from `release/1.0`, merged into it by pull request, and the same fix goes to `main` by its own pull request (`git cherry-pick -x <commit>` on a branch from `main`). The version bump to `1.0.1` is a pull request into `release/1.0`, and the tag goes on that branch's merged commit. A tag `v1.0.1` on any other branch, or `v1.1.0` on `release/1.0`, is refused (`.github/scripts/release-line.py`).
3. **The workflow runs** on that tag only (never on a pull request, a branch or a fork):
   - `verify`: the tag must be `vX.Y.Z` or `vX.Y.Z-(alpha|beta|rc).N`, equal to the version in `pyproject.toml`, `package.json` and `package-lock.json`, and the tagged commit must be on `main`, or on `release/X.Y` with the tag's own X.Y;
   - `ci`: all of `ci.yml` on the tagged commit (end-to-end tests, the walkthrough and the image checks included);
   - `publish`, only when both are green: builds one `linux/amd64` image with the build date (the commit time) and commit, smoke-tests it and checks its labels, refuses to go on if `X.Y.Z` is already in the registry (or if the registry cannot say), decides the aliases before logging in (`.github/scripts/image-aliases.py`, printed in the log: `X.Y` when this is the highest plain `vX.Y.*` tag, `X` when the highest plain `vX.*`, `latest` when the highest plain tag of all, `X` and `latest` only from major 1; a suffixed version only as itself, D-170 (4)), and stops with nothing pushed if it cannot decide (the tag being released not among the tags read, for instance); then it pushes `ghcr.io/horo86/portfolio-tracker:X.Y.Z` and its aliases in one `docker push --all-tags`, with the workflow's own `GITHUB_TOKEN`, pulls the image back by digest and checks its health again.
   - One release runs at a time: a second tag waits for the first run to end. GitHub keeps only one run waiting, so a third tag pushed meanwhile cancels the waiting one: **push one tag at a time**, and rerun a cancelled release with "Re-run all jobs". The aliases are decided in the publish job, on its own fetch of the tags, so a re-run of an older release never moves one back.
   - A published version is never pushed again, so re-running a release that already pushed fails; fix a broken release with the next patch version. The registry still receives the names one by one, so a push that breaks midway can leave `X.Y.Z` published without some alias; the aliases then catch up with the next patch (no manual retag with a personal token).
4. **On GitHub,** the maintainer creates the release from the tag (marked pre-release for a preview version), with the version's notes as its text: the release run's summary shows them, and its `release-notes` artifact holds them as a file. After the first push the maintainer makes the package public (Packages → portfolio-tracker → Package settings).
5. **After a stable release** (not an rc; also after 1.0.0-rc.2, the first release after the baseline, D-173), in the next pull request (D-168 (7)):
   - its upgrade fixture, made on the tag's own commit: `cd backend && uv run python scripts/make_upgrade_fixture.py --development --version X.Y.Z --work .data-fixture` (invented data only; it writes `tests/fixtures/upgrade/X.Y.Z.sqlite`, its expected figures and its `index.json` entry). A release that changed neither the schema nor the shape of the data may instead add `{"version": "X.Y.Z", "same_as": "<earlier version>"}` to `index.json` by hand;
   - `.github/upgrade-from.txt` names the new image pinned by its digest, on an `upgrade <image>` line replacing the previous one (the release run prints `pushed …@sha256:…`), so CI upgrades a data folder of that image from then on. The `refuse <image>` line (the 0.9.0 image) stays: CI checks that a preview's folder is refused untouched (D-173).

   The release workflow refuses a stable tag while an earlier stable release has no fixture (`.github/scripts/upgrade-fixture-gate.py`); the previews 0.9.0 and 1.0.0-rc.1 are listed in `index.json` under `unsupported` instead (D-173).

Every action in the workflows is pinned to a full commit SHA, with its version in a comment.

## Contributing
Documentation, code and commit messages are in English; the UI texts live in `frontend/src/locales/` (Italian and English). **This repository is public: never commit real financial data or real broker exports (even anonymized)** — use the synthetic fixtures in `tests/fixtures/`. Real files for local checks go in `private/`, which is git-ignored.
