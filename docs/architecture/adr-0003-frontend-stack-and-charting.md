# ADR-0003: Frontend stack and charting

Status: accepted (M0, October 2026)

## Context
Visual-first UX: dashboard, historical value chart (ranges up to "max" = up to 20 years of daily points), allocations, income history; responsive; WCAG 2.1 AA on the main flows; Italian and English; light and dark themes; served by the same container (`../requirements.md` § User Experience, § Dashboard; D-26).

## Options considered
- **React + TypeScript + Vite (SPA)** — large ecosystem, strong typing against the OpenAPI contract, fast build; output is static files that FastAPI serves (Node only at build time).
- **htmx + server templates** — rejected by the maintainer: interactive tables and charts would need a JavaScript layer anyway.
- Charting:
  - **Apache ECharts** — canvas (or SVG) renderer that stays smooth with ~7,300 points per series × several series; built-in time axis, data zoom / range selection, dark theme, ARIA descriptions (`aria` option) and decal patterns for colour-blind users; modular imports keep the bundle reasonable; Apache-2.0.
  - **Recharts** — idiomatic React components, MIT, but SVG-only: one DOM node per point makes long daily histories slow, and zoom/range selection must be built by hand.

## Decision
- **React 19.3.0**, **TypeScript 6.0.3** (the 7.x line is not yet supported by typescript-eslint), **Vite 8.3.4** with **@vitejs/plugin-react 6.1.2**.
- **i18next 26.4.3** + **react-i18next 17.0.16**; translation files `frontend/src/locales/it.json` and `en.json`; no hard-coded UI text.
- Theme: CSS custom properties on `:root`, a dark variant via `prefers-color-scheme` and an explicit `data-theme` attribute (user choice light/dark/system).
- Charting: **Apache ECharts 6.1.0** (via modular `echarts/core` imports), added in M5 when the first chart is built — not in M0.
- Lint: ESLint 10.12.0 + typescript-eslint 8.71.1 + eslint-plugin-react-hooks 7.1.1. Tests: Vitest 5.0.3 + Testing Library (react 16.3.3, dom 10.4.2, jest-dom 7.0.1) + jsdom 30.1.2.
- Node is used only at build time: **Node 24 LTS** (24.21.0) in the Docker build stage and in CI.

## Consequences
- Easier: one SPA for web now and the same API for future mobile apps; charts scale to the full history.
- Harder: ECharts is not a React component library — a thin wrapper component is needed (M5); its bundle is larger than Recharts, mitigated by modular imports and route-level code splitting.
