// The one Node function the market spec uses to open a gate of the fake price source
// (backend/tests/e2e_market_server.py). Declared here: the frontend has no @types/node.
declare module "node:fs" {
  export function writeFileSync(path: string, data: string): void;
}
