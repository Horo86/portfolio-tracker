// The one wrapper around fetch for the API (ADR-0004, ADR-0005).
// Writes carry the CSRF header; errors become ApiError with the stable `code` only:
// the server's `detail` is an English hint for developers and is never shown.

export const CSRF_HEADER = "X-Requested-With";
export const CSRF_VALUE = "portfolio-tracker";

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

const CODE_PATTERN = /^[A-Z0-9_]{1,64}$/;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly extra: Record<string, unknown>;
  readonly retryAfter: number | null; // seconds, from the Retry-After header

  constructor(
    status: number,
    code: string,
    extra: Record<string, unknown> = {},
    retryAfter: number | null = null,
  ) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.extra = extra;
    this.retryAfter = retryAfter;
  }
}

type SessionHandlers = {
  // The session is gone (expired, revoked, logged out elsewhere): back to login.
  onUnauthorized?: () => void;
  // A temporary password must be changed first.
  onPasswordChangeRequired?: () => void;
};

let handlers: SessionHandlers = {};

export function setSessionHandlers(next: SessionHandlers): void {
  handlers = next;
}

function url(path: string): string {
  return path.startsWith("/api/") ? path : `/api/v1${path}`;
}

function retryAfterSeconds(response: Response): number | null {
  const value = response.headers.get("Retry-After");
  if (value === null || !/^\d+$/.test(value.trim())) return null;
  return Number.parseInt(value, 10);
}

async function toError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // not JSON (a proxy error page, an empty body)
  }
  const retryAfter = retryAfterSeconds(response);
  if (body && typeof body === "object" && "code" in body && typeof body.code === "string") {
    const code = CODE_PATTERN.test(body.code) ? body.code : "HTTP_ERROR";
    const extra = Object.fromEntries(
      Object.entries(body).filter(([key]) => key !== "code" && key !== "detail"),
    );
    return new ApiError(response.status, code, extra, retryAfter);
  }
  return new ApiError(
    response.status,
    response.status >= 500 ? "SERVER" : "HTTP_ERROR",
    {},
    retryAfter,
  );
}

export async function api<T>(
  method: Method,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers[CSRF_HEADER] = CSRF_VALUE;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(url(path), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(0, "NETWORK");
  }

  if (!response.ok) {
    const error = await toError(response);
    if (error.status === 401 && error.code === "NOT_AUTHENTICATED") handlers.onUnauthorized?.();
    if (error.status === 403 && error.code === "PASSWORD_CHANGE_REQUIRED") {
      handlers.onPasswordChangeRequired?.();
    }
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
