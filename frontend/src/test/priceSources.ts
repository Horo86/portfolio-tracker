import type { MarketDataRunStatus, PriceEngine, PriceEngines, PriceRoutes } from "../api/types";
import { type Handler, stubFetch } from "./fetchStub";
import { HEALTH, ME, NO_ACCOUNTS } from "./renderApp";

// The price-sources page (D-196) in unit tests: the three engines of this version with
// invented health and counts, never an ISIN or a symbol.

export const ADMIN = { ...ME, role: "ADMINISTRATOR" } as const;

export const engine = (name: string, fields: Partial<PriceEngine> = {}): PriceEngine => ({
  name,
  roles: ["ETF_STOCK"],
  official: true,
  terms_url: null,
  enabled: true,
  off_by_environment: false,
  needs_credential: false,
  credential_set: false,
  status: "WORKING",
  last_success_at: null,
  last_error_code: null,
  last_error_at: null,
  paused_until: null,
  checks: {},
  ...fields,
});

export const ENGINES: PriceEngines = {
  market_data: "ON",
  engines: [
    engine("frankfurter", { roles: ["FX"], terms_url: "https://frankfurter.dev" }),
    engine("openfigi", {
      roles: ["CLASSIFY"],
      terms_url: "https://www.openfigi.com/docs/terms-of-service",
      checks: { OK: 4 },
    }),
    engine("yahoo", {
      roles: ["ETF_STOCK", "CLASSIFY"],
      official: false,
      last_success_at: "2026-11-19T17:05:00Z",
      checks: { OK: 3, NOT_FOUND: 1 },
    }),
  ],
};

export const ROUTES: PriceRoutes = {
  market_data: "ON",
  routes: { ETF_STOCK: ["yahoo"], BOND: [], FX: ["frankfurter"], CLASSIFY: ["openfigi", "yahoo"] },
  available: { ETF_STOCK: ["yahoo"], BOND: [], FX: ["frankfurter"], CLASSIFY: ["openfigi", "yahoo"] },
  exchanges: ["GER", "MIL"],
};

export const STATUS: MarketDataRunStatus = {
  market_data: "ON",
  daily_time_utc: "18:30:00",
  running: null,
  next_run_at: "2026-11-20T18:30:00Z",
  recheck_pending: false,
  last_run: {
    kind: "DAILY",
    result: "OK",
    started_at: "2026-11-19T18:30:00Z",
    finished_at: "2026-11-19T18:31:00Z",
    counts: {},
    errors: {},
  },
  engines: [],
  routes: { ETF_STOCK: ["yahoo"], BOND: [], FX: ["frankfurter"], CLASSIFY: ["openfigi", "yahoo"] },
};

/** The page's requests answered from these fixtures; `extra` replaces or adds routes. */
export function stubPriceSources(extra: Record<string, Handler> = {}) {
  return stubFetch({
    "GET /api/v1/me": { body: ADMIN },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": NO_ACCOUNTS,
    "GET /api/v1/admin/market-data/status": { body: STATUS },
    "GET /api/v1/admin/market-data/engines": { body: ENGINES },
    "GET /api/v1/admin/market-data/routes": { body: ROUTES },
    ...extra,
  });
}
