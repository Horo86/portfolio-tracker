import { vi } from "vitest";

// A stubbed fetch for unit tests (no MSW, M5 planning): each route answers with a status,
// a JSON body and optional headers. Unknown routes fail the test loudly.

export type Reply = { status?: number; body?: unknown; headers?: Record<string, string> };
export type Call = { method: string; path: string; headers: Headers; body: unknown };
type Handler = Reply | ((call: Call) => Reply);

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
    if (handler === undefined) throw new Error(`unexpected request: ${method} ${path}`);
    const reply = typeof handler === "function" ? handler(call) : handler;
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
