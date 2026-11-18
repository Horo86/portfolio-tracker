// The throwaway demo server shared by the opt-in screenshot scripts (readme-screenshots.mjs,
// review-screenshots.mjs); never in CI. It fills a new `.data…` folder (ignored by git) with
// the invented demo data of backend/scripts/dev_data.py and starts the built app on it, with
// market data and the two-factor obligation off. No real data is used anywhere.
import { spawn, spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";

export const FRONTEND = fileURLToPath(new URL("..", import.meta.url));
const BACKEND = `${FRONTEND}/../backend`;
const PYTHON = `${BACKEND}/.venv/bin/python`;
export const CSRF = { "X-Requested-With": "portfolio-tracker" };
// Public, invented credentials of the demo data (dev_data.py).
export const DEMO = { username: "demo", password: "dev-demo-password" };
export const ADMIN = { username: "admin", password: "dev-admin-password" };

/** Fails when something already listens on the port: the shots would be of another server. */
export function portFree(port) {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", (error) => reject(new Error(`port ${port} is in use (${error.message})`)));
    probe.listen(port, "127.0.0.1", () => probe.close(() => resolve()));
  });
}

/** Waits until the server answers, or fails if it exits first. */
export async function ready(server, base) {
  let exited = false;
  server.once("exit", () => (exited = true));
  for (let i = 0; i < 120; i++) {
    if (exited) throw new Error("the server exited before it answered");
    const ok = await fetch(`${base}/api/health`).then((r) => r.ok, () => false);
    if (ok) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("the server did not answer within 60 s");
}

export async function stop(server) {
  if (server.exitCode !== null) return;
  const gone = new Promise((resolve) => server.once("exit", resolve));
  server.kill("SIGTERM"); // by its own PID, the process this script started
  await gone;
}

/** Seeds `data` (deleted first) and starts the app on it; returns the server's process. */
export async function startDemo(data, port) {
  await portFree(port);
  rmSync(data, { recursive: true, force: true });
  const env = { ...process.env, PT_DATA_DIR: data, PT_MARKET_DATA: "false", PT_REQUIRE_2FA: "off" };
  const seeded = spawnSync(PYTHON, ["scripts/dev_data.py", "--development"], {
    cwd: BACKEND,
    env,
    stdio: "ignore",
  });
  if (seeded.status !== 0) {
    rmSync(data, { recursive: true, force: true });
    throw new Error(`dev_data.py failed (exit ${seeded.status})`);
  }
  const server = spawn(PYTHON, ["-m", "portfolio_tracker"], {
    cwd: BACKEND,
    env: {
      ...env,
      PT_HOST: "127.0.0.1",
      PT_PORT: String(port),
      PT_STATIC_DIR: `${FRONTEND}/dist`,
      PT_COOKIE_SECURE: "false",
    },
    stdio: "ignore",
  });
  console.log(`server started, PID ${server.pid}`);
  return server;
}

/** Re-dates the demo's manual prices to yesterday (UTC), on this throwaway copy only, so no
 * security shows "price not updated" (maintainer, README screenshots). */
export async function freshPrices(api) {
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  const assets = await (await api.get("/api/v1/assets")).json();
  for (const asset of assets) {
    const prices = await (await api.get(`/api/v1/assets/${asset.id}/prices`)).json();
    const last = prices[0]; // one manual price per demo security
    if (!last) continue;
    const put = await api.put(`/api/v1/assets/${asset.id}/prices`, {
      data: { day: yesterday, close: last.close },
      headers: CSRF,
    });
    if (!put.ok()) throw new Error(`price of ${asset.name} refused (${put.status()})`);
  }
}
