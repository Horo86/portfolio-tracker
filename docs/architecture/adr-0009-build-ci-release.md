# ADR-0009: Build, CI/CD and release

Status: accepted (M0, October 2026)

## Context
Reproducible build, automated validation, Docker image, license check for AGPL compatibility, minimal manual steps (`../requirements.md` § CI/CD; D-23, D-25).

## Decision
- **Lockfiles** committed: `backend/uv.lock` (uv 0.13.0) and `frontend/package-lock.json` (npm). Every dependency version is pinned exactly.
- **Dockerfile** (multi-stage, `linux/amd64`): (1) `node:24.21.0-alpine` builds the frontend; (2) `python:3.12.15-slim-trixie` + `uv` (copied from `ghcr.io/astral-sh/uv:0.13.0`) builds the backend virtual environment from the lockfile; (3) a final `python:3.12.15-slim-trixie` stage with only the virtual environment, the backend source and the built frontend; non-root user (UID 10001 by default, overridable with `user:` in Compose); `VOLUME /data`; `HEALTHCHECK` on `/api/health`.
- **GitHub Actions** (`.github/workflows/ci.yml`), on push and pull request, read-only permissions, no secrets: backend (ruff lint + format check, pytest, license check), frontend (ESLint, type check, Vitest, build, license check), image (build, start, health check, size report). Actions pinned to release tags: actions/checkout v7.0.1, actions/setup-node v7.1.0, astral-sh/setup-uv v10.3.0, docker/setup-buildx-action v4.4.1.
- **License policy:** dependencies (runtime and build) must use licenses compatible with AGPL v3. Allowlist: MIT, MIT-0, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, PSF-2.0/Python-2.0, MPL-2.0, LGPL-2.1/3.0, 0BSD, CC0-1.0, Unlicense, BlueOak-1.0.0, CC-BY-4.0 (data files only). Checked with pip-licenses 5.5.5 and license-checker-rseidelsohn 5.0.1; anything else fails CI until reviewed. MIT-0 (MIT without the attribution condition) is used by `cffi` (2.1.1, runtime dependency of argon2-cffi); the backend check accepts it through its `MIT` entry (`--partial-match`), the frontend check lists it explicitly. Documented exceptions (development-only, never shipped in the image or the bundle): `spdx-exceptions@2.5.0` (CC-BY-3.0) and `spdx-ranges@2.1.1` (MIT AND CC-BY-3.0), the SPDX data files of the license checker itself; they are excluded by exact version so any upgrade is reviewed again. `caniuse-lite` (CC-BY-4.0, browser data used by lint tooling) is allowed as a data-only license.
- **Releases** (M7): semantic version tags trigger an image build and publish; no publishing before M7.

## Consequences
- Easier: identical builds locally and in CI; license problems surface on the pull request that introduces them.
- Harder: version bumps are explicit pull requests (a dependency update bot can be added later).
