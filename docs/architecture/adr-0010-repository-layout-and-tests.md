# ADR-0010: Repository layout and test strategy

Status: accepted (M0, October 2026)

## Context
A small project with one deployable; calculation code is test-first and every example in `../domain-rules.md` §16 is an automated test (CLAUDE.md hard rule 4).

## Options considered
- **Monorepo** with backend and frontend side by side — one pull request can change API and UI together; one CI.
- **Separate repositories** — independent release cycles that this project does not need.

## Decision
```
backend/                 Python package (src layout): src/portfolio_tracker/ (incl. migrations/), tests/
frontend/                React + Vite app: src/, src/locales/
docs/                    requirements, domain, decisions, import specs, architecture (ADRs), ui
tests/fixtures/          synthetic fixtures shared by backend tests (e.g. directa/)
private/                 git-ignored real files for optional local checks only
Dockerfile, docker-compose.example.yml, .github/workflows/ci.yml
```
- Backend tests: pytest under `backend/tests/`; the calculation engine (M1) is a pure package with no database or web imports, tested first with the §16 examples, plus property tests for ordering and edits (hypothesis, D-54). API tests use a temporary SQLite file per test.
- Fixture tests read `tests/fixtures/`; optional real-file tests read `private/` and **skip** when it is empty.
- Frontend tests: Vitest + Testing Library in jsdom; accessibility checks on the main flows in M5.

## Consequences
- Easier: one place for docs, code and fixtures; one CI workflow.
- Harder: CI runs both stacks on every change (acceptable at this size).
