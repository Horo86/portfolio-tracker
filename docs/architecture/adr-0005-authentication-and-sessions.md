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
  - *M2 minimum (before M6):* the same 401 `INVALID_CREDENTIALS` and a comparable time (a dummy Argon2 verification) for an unknown user, a disabled user and a wrong password; an in-memory throttle per username and per direct-peer IP: after 5 consecutive failures a 1-minute block, doubling on each further failure up to 15 minutes (429 `TOO_MANY_ATTEMPTS` with `Retry-After`); a success clears the username counter only; counters are forgotten after 30 minutes without failures. Configurable thresholds and trusted proxies are M6.
- Authorization: every query is filtered by the owner user at the repository layer, from the first endpoint in M2.
- First run: setup wizard — create the first Administrator **or** restore from a backup (D-34).

## Consequences
- Easier: revocation, admin password reset, "log out everywhere" are single queries.
- Harder: a database lookup per request (cheap with an index on the token hash).
