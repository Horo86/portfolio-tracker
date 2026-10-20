import { vi } from "vitest";

// A stubbed fetch for unit tests (no MSW, M5 planning): each route answers with a status,
// a JSON body and optional headers. A request to a route not listed is recorded and fails
// the test after it ends (test/setup.ts); the page sees it as a network error.

let unexpected: string[] = [];

/** The unexpected requests since the last call (checked after every test). */
export function takeUnexpected(): string[] {
  const found = unexpected;
  unexpected = [];
  return found;
}

export type Reply = { status?: number; body?: unknown; headers?: Record<string, string> };
export type Call = { method: string; path: string; headers: Headers; body: unknown };
// A handler may answer later (a Promise), to test answers that arrive late.
export type Handler = Reply | ((call: Call) => Reply | Promise<Reply>);

export function stubFetch(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? "GET").toUpperCase();
    const path = String(input);
    const call: Call = {
      method,
      path,
      headers: new Headers(init.headers),
      body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
    };
    calls.push(call);
    const handler = routes[`${method} ${path}`];
    if (handler === undefined) {
      unexpected.push(`${method} ${path}`);
      throw new TypeError("unexpected request");
    }
    const reply = await (typeof handler === "function" ? handler(call) : handler);
    const status = reply.status ?? 200;
    const body = status === 204 || reply.body === undefined ? null : JSON.stringify(reply.body);
    return new Response(body, {
      status,
      headers: { "Content-Type": "application/json", ...reply.headers },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}
