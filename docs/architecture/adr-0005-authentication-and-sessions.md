# ADR-0005: Authentication and session implementation

Status: accepted (M0, October 2026) — design only; basic auth is built in M2, 2FA and hardening in M6 (D-50)

## Context
D-21, D-21c, D-33, D-34, D-51; `../requirements.md` § Authentication and Security. No mail server; same model for future mobile clients; the backend enforces everything.

## Options considered
- **Opaque random session tokens stored hashed in the database** — revocable at once ("log out everywhere"), simple, no key management.
- **JWT access + refresh tokens** — stateless verification, but revocation needs a deny list, i.e. state anyway; more ways to get it wrong.
- **External identity provider (OIDC)** — a second service to run; out of proportion for V1 (could be an optional V2 integration).

## Decision
- Passwords: **Argon2id** via argon2-cffi (25.1.0 at the time of writing; parameters from the library's RFC 9106 low-memory profile, re-tuned on the reference hardware in M2); minimum 12 characters, no composition rules.
- Sessions: 256-bit random tokens; only a SHA-256 hash is stored, with user, creation, last use, expiry, user agent. Web: `HttpOnly; Secure; SameSite=Lax` cookie plus a CSRF header check on state-changing requests; future clients: `Authorization: Bearer`. Session duration configurable in the admin area (D-51).
  - *M2 implementation:* the CSRF header is `X-Requested-With: portfolio-tracker`, required on every cookie-authenticated request that is not GET/HEAD/OPTIONS. Expiry defaults: 7 days without use or 30 days after login, whichever first (`PT_SESSION_IDLE_DAYS`, `PT_SESSION_ABSOLUTE_DAYS`; admin setting in M6). The web login sets only the cookie; a client that asks for a token (`issue_token: true`) gets it in the body and no cookie.
  - *Cookie `Secure`:* on by default (`PT_COOKIE_SECURE=true`). Browsers drop Secure cookies over plain http except on `localhost`, so a LAN user opening `http://<ip>:8000` would loop back to the login page. The app logs a notice at startup and a warning (once per process) when a login is served over plain http to a non-localhost host; the README explains HTTPS or `PT_COOKIE_SECURE=false` on a trusted LAN.
- TOTP: pyotp (2.10.0 at the time of writing), 10 single-use recovery codes stored hashed (M6).
- Rate limiting: per account and per IP with growing delays and lockout thresholds from the admin area; client IP from `X-Forwarded-For` **only** when the direct peer is in the trusted-proxy list given by an environment variable (list of networks) (D-51).
  - *M2 minimum (before M6, D-82):* the same 401 `INVALID_CREDENTIALS` and a comparable time (a dummy Argon2 verification) for an unknown user, a disabled user and a wrong password. An in-memory throttle counts failed password checks per username and per client IP (login) and per user (current-password check of the password change). **Thresholds: 5 consecutive failures per username/user, 30 per IP**: behind a reverse proxy the whole household shares one IP, so the IP threshold must not let a few typos lock everyone out. Past a threshold: a 1-minute block, doubling on each further failure up to 15 minutes (429 `TOO_MANY_ATTEMPTS` with `Retry-After`). Attempts are reserved before the check (parallel requests cannot exceed the free attempts). A success clears the username/user counter only; counters are forgotten after 30 minutes without failures. The server runs with `proxy_headers=False`, so the IP is the direct peer until trusted proxies arrive (M6). Configurable thresholds are M6.
  - *Transactions:* the request transaction is committed before the response is sent (a failed commit is a 500, never a false success). Password hashing and verification never run inside a write transaction (BEGIN IMMEDIATE holds the SQLite write lock): login, setup and the password change read in a short read transaction, hash outside, then write in a short write transaction that re-checks what it read. Session resolution is read-only; read requests refresh `last_used_at` (at most every 5 minutes) in a separate best-effort write that is skipped when the database is busy; expired sessions are rejected and left for a cleanup job (M4).
- Authorization: every query is filtered by the owner user at the repository layer, from the first endpoint in M2.
  - *M2 implementation:* another user's resource and admin endpoints for non-administrators answer 404, never 403 (D-92); `backend/tests/test_route_matrix.py` checks every route of the OpenAPI document (401 without a session, 404 and no change for another investor and for the Administrator) and fails when a route is not classified. A temporary password must be changed before any other endpoint can be used (D-93).
- First run: setup wizard — create the first Administrator **or** restore from a backup (D-34).

## Consequences
- Easier: revocation, admin password reset, "log out everywhere" are single queries.
- Harder: a database lookup per request (cheap with an index on the token hash).
