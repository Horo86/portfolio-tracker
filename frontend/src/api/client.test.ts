import { afterEach, describe, expect, it, vi } from "vitest";

import { stubFetch } from "../test/fetchStub";
import { api, ApiError, CSRF_HEADER, CSRF_VALUE, setSessionHandlers } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
  setSessionHandlers({});
});

async function caught(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("no error thrown");
}

describe("api client", () => {
  it("sends the CSRF header on writes only, same-origin, under /api/v1", async () => {
    const { calls, fetchMock } = stubFetch({
      "GET /api/v1/me": { body: { id: "1" } },
      "POST /api/v1/accounts": { status: 201, body: { id: "a" } },
      "PATCH /api/v1/me": { body: {} },
      "DELETE /api/v1/accounts/a": { body: { deleted_transactions: 0 } },
      "GET /api/health": { body: { status: "ok" } },
    });

    await api("GET", "/me");
    await api("POST", "/accounts", { name: "Conto", currency: "EUR" });
    await api("PATCH", "/me", { theme: "ambra" });
    await api("DELETE", "/accounts/a", { confirm_name: "Conto" });
    await api("GET", "/api/health");

    expect(calls.map((c) => [c.method, c.headers.get(CSRF_HEADER)])).toEqual([
      ["GET", null],
      ["POST", CSRF_VALUE],
      ["PATCH", CSRF_VALUE],
      ["DELETE", CSRF_VALUE],
      ["GET", null],
    ]);
    expect(calls[1].body).toEqual({ name: "Conto", currency: "EUR" });
    expect(calls[1].headers.get("Content-Type")).toBe("application/json");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "same-origin" });
  });

  it("returns undefined for 204 answers", async () => {
    stubFetch({ "POST /api/v1/auth/logout": { status: 204 } });
    await expect(api("POST", "/auth/logout")).resolves.toBeUndefined();
  });

  it("keeps the code and the extra fields, never the detail", async () => {
    stubFetch({
      "POST /api/v1/auth/login": {
        status: 401,
        body: { code: "INVALID_CREDENTIALS", detail: "wrong username or password", hint: 1 },
      },
    });
    const error = await caught(api("POST", "/auth/login", {}));
    expect(error).toMatchObject({ status: 401, code: "INVALID_CREDENTIALS", extra: { hint: 1 } });
    expect(JSON.stringify(error.extra)).not.toContain("wrong username");
    expect(error.message).toBe("INVALID_CREDENTIALS");
  });

  it("reads Retry-After in seconds", async () => {
    stubFetch({
      "POST /api/v1/auth/login": {
        status: 429,
        body: { code: "TOO_MANY_ATTEMPTS", detail: "x", retry_after: 90 },
        headers: { "Retry-After": "90" },
      },
    });
    const error = await caught(api("POST", "/auth/login", {}));
    expect(error.code).toBe("TOO_MANY_ATTEMPTS");
    expect(error.retryAfter).toBe(90);
  });

  it("maps answers without a JSON code and unreachable servers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502 })));
    expect(await caught(api("GET", "/me"))).toMatchObject({ status: 502, code: "SERVER" });

    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 400 })));
    expect((await caught(api("GET", "/me"))).code).toBe("HTTP_ERROR");

    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ code: "<b>odd</b>" }, { status: 400 })));
    expect((await caught(api("GET", "/me"))).code).toBe("HTTP_ERROR");

    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("failed to fetch"))));
    expect(await caught(api("GET", "/me"))).toMatchObject({ status: 0, code: "NETWORK" });
  });

  it("calls the session handlers on 401 NOT_AUTHENTICATED and 403 PASSWORD_CHANGE_REQUIRED", async () => {
    const onUnauthorized = vi.fn();
    const onPasswordChangeRequired = vi.fn();
    setSessionHandlers({ onUnauthorized, onPasswordChangeRequired });
    stubFetch({
      "GET /api/v1/me": { status: 401, body: { code: "NOT_AUTHENTICATED", detail: "x" } },
      "GET /api/v1/accounts": {
        status: 403,
        body: { code: "PASSWORD_CHANGE_REQUIRED", detail: "x" },
      },
      "POST /api/v1/auth/login": { status: 401, body: { code: "INVALID_CREDENTIALS" } },
      "POST /api/v1/accounts": { status: 403, body: { code: "CSRF_CHECK_FAILED" } },
    });

    await caught(api("GET", "/me"));
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    await caught(api("GET", "/accounts"));
    expect(onPasswordChangeRequired).toHaveBeenCalledTimes(1);

    // A wrong password at login and a CSRF refusal are not session changes.
    await caught(api("POST", "/auth/login", {}));
    await caught(api("POST", "/accounts", {}));
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(onPasswordChangeRequired).toHaveBeenCalledTimes(1);
  });
});
