import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { api } from "../api/client";
import { stubFetch, type Handler } from "../test/fetchStub";
import { DASHBOARD, HEALTH, ME, NO_ACCOUNTS, NO_SESSION, renderApp } from "../test/renderApp";
import { groupSecret, qrPath, secondFactor } from "../twoFactor";

// Two-factor authentication in the browser (D-174): the login's second step, the forced
// enrolment of an administrator and the guard. Invented secrets and codes only.

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

const ADMIN = { ...ME, role: "ADMINISTRATOR" } as const;
const STEP = { body: { two_factor_required: true, challenge: null, expires_in: 300 } };
const SESSION = { body: { user: ME, token: null, expires_at: "2026-12-01T00:00:00Z" } };
const wrong = (attemptsLeft: number) => ({
  status: 401,
  body: { code: "TWO_FACTOR_CODE_WRONG", detail: "wrong code", attempts_left: attemptsLeft },
});

const SECRET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const RECOVERY = Array.from({ length: 10 }, (_, i) => `Ab${i}c-Def${i}-Ghj${i}-Kmn${i}`);

/** Every cached query and mutation, to check no secret is kept there (D-174 (10)). */
function cached(client: ReturnType<typeof renderApp>["client"]): string {
  return JSON.stringify([
    client.getMutationCache().getAll().map((m) => [m.state, m.options.mutationKey]),
    client.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]),
  ]);
}

describe("login, second step", () => {
  function loggedOut(routes: Record<string, Handler>) {
    // The session exists once the second step answered 200 (its cookie is set).
    let session = false;
    const step = routes["POST /api/v1/auth/login/two-factor"];
    return stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": () => (session ? { body: ME } : NO_SESSION),
      "GET /api/v1/setup/status": { body: { setup_required: false } },
      "POST /api/v1/auth/login": STEP,
      ...routes,
      "POST /api/v1/auth/login/two-factor": async (call) => {
        const reply = await (typeof step === "function" ? step(call) : (step ?? NO_SESSION));
        session = (reply.status ?? 200) === 200;
        return reply;
      },
    });
  }
  async function toSecondStep() {
    renderApp("/login");
    await screen.findByRole("heading", { name: "Log in" });
    type("Username", "mario");
    type("Password", "the right password");
    click("Log in");
    return screen.findByRole("heading", { name: "Two-step verification" });
  }

  it("asks for the code after the password, then opens the session", async () => {
    let attempts = 0;
    const { calls } = loggedOut({
      "POST /api/v1/auth/login/two-factor": () => (++attempts === 1 ? wrong(4) : SESSION),
    });
    const heading = await toSecondStep();
    expect(heading).toHaveFocus(); // a new step, announced like a new page
    const field = screen.getByLabelText("Code");
    expect(field).toHaveAttribute("autocomplete", "one-time-code");
    expect(field).toHaveAttribute("inputmode", "numeric");

    type("Code", "111 111");
    click("Confirm");
    expect(
      await screen.findByText(
        "The code is wrong, or it was already used. Wait for the next code and try again. 4 attempts left.",
      ),
    ).toHaveAttribute("role", "alert");
    expect(screen.getByLabelText("Code")).toHaveValue("");
    // Still on the step: a wrong code is not the end of a session.
    expect(screen.getByRole("heading", { name: "Two-step verification" })).toBeInTheDocument();

    type("Code", "222222");
    click("Confirm");
    expect(await screen.findByRole("heading", { name: DASHBOARD })).toBeInTheDocument();
    const steps = calls.filter((c) => c.path === "/api/v1/auth/login/two-factor");
    // The web app sends no proof: it is in the HttpOnly cookie. Spaces are left out.
    expect(steps.map((c) => c.body)).toEqual([{ code: "111111" }, { code: "222222" }]);
    expect(steps.every((c) => c.headers.get("X-Requested-With") === "portfolio-tracker")).toBe(true);
  });

  it("checks the six digits before sending", async () => {
    const { calls } = loggedOut({});
    await toSecondStep();
    click("Confirm");
    expect(screen.getByText("Fill in this field.")).toBeInTheDocument();
    type("Code", "12345");
    click("Confirm");
    expect(screen.getByText("Enter the 6 digits.")).toBeInTheDocument();
    expect(screen.getByLabelText("Code")).toHaveFocus();
    expect(calls.some((c) => c.path.endsWith("/two-factor"))).toBe(false);
  });

  it("takes a recovery code instead, and back", async () => {
    const { calls } = loggedOut({ "POST /api/v1/auth/login/two-factor": SESSION });
    await toSecondStep();
    click("Lost the app? Use a recovery code.");
    const field = screen.getByLabelText("Recovery code");
    expect(field).toHaveFocus();
    expect(field).toHaveAttribute("autocomplete", "off");
    expect(screen.getByText("Enter one of your recovery codes: each works once.")).toBeInTheDocument();
    click("Use the app's code");
    expect(screen.getByLabelText("Code")).toBeInTheDocument();
    click("Lost the app? Use a recovery code.");
    type("Recovery code", " Abcd-Efgh-Jkmn-Pqrs ");
    click("Confirm");
    expect(await screen.findByRole("heading", { name: DASHBOARD })).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/two-factor"))?.body).toEqual({
      recovery_code: "Abcd-Efgh-Jkmn-Pqrs",
    });
  });

  it("goes back to the password after the last wrong code", async () => {
    loggedOut({ "POST /api/v1/auth/login/two-factor": wrong(0) });
    await toSecondStep();
    type("Code", "333333");
    click("Confirm");
    expect(await screen.findByRole("heading", { name: "Log in" })).toHaveFocus();
    expect(
      screen.getByText("Too many wrong codes: enter your password again."),
    ).toHaveAttribute("role", "alert");
    expect(screen.getByLabelText("Username")).toHaveValue("mario");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("goes back to the password when the step expired", async () => {
    loggedOut({
      "POST /api/v1/auth/login/two-factor": {
        status: 401,
        body: { code: "LOGIN_STEP_EXPIRED", detail: "log in again" },
      },
    });
    await toSecondStep();
    type("Code", "444444");
    click("Confirm");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(
      screen.getByText("The time for the code ran out: enter your password again."),
    ).toBeInTheDocument();
  });

  it("says how long to wait when the codes are blocked", async () => {
    loggedOut({
      "POST /api/v1/auth/login/two-factor": {
        status: 429,
        body: { code: "TOO_MANY_ATTEMPTS", detail: "too many wrong codes", retry_after: 120 },
        headers: { "Retry-After": "120" },
      },
    });
    await toSecondStep();
    type("Code", "555555");
    click("Confirm");
    expect(
      await screen.findByText("Too many attempts. Try again in 2 minutes."),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Two-step verification" })).toBeInTheDocument();
  });

  it("keeps no code or password in the caches", async () => {
    loggedOut({ "POST /api/v1/auth/login/two-factor": wrong(3) });
    const { client } = renderApp("/login");
    await screen.findByRole("heading", { name: "Log in" });
    type("Username", "mario");
    type("Password", "the right password");
    click("Log in");
    await screen.findByRole("heading", { name: "Two-step verification" });
    type("Code", "666666");
    click("Confirm");
    await screen.findByText(/3 attempts left/);
    const kept = cached(client);
    expect(kept).not.toContain("666666");
    expect(kept).not.toContain("the right password");
  });
});

describe("enrolment of an administrator", () => {
  function obliged(routes: Record<string, Handler> = {}, me: object = {}) {
    let user: object = { ...ADMIN, two_factor_setup_required: true, ...me };
    const stub = stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": () => ({ body: user }),
      "POST /api/v1/me/two-factor/setup": {
        body: {
          secret: SECRET,
          otpauth_uri: `otpauth://totp/Portfolio%20Tracker:mario?secret=${SECRET}`,
          qr: ["1110111", "1000001", "1011101", "0000000", "1011101", "1000001", "1110111"],
          digits: 6,
          period: 30,
          server_time: new Date().toISOString(),
        },
      },
      "POST /api/v1/me/two-factor/confirm": () => {
        user = { ...ADMIN, two_factor_enabled: true, recovery_codes_left: 10 };
        return { body: { user, token: null, recovery_codes: RECOVERY } };
      },
      "POST /api/v1/auth/logout": { status: 204 },
      ...routes,
    });
    return stub;
  }

  it("is where the guard takes an administrator who must enrol, after the password", async () => {
    obliged({}, { must_change_password: true });
    renderApp("/accounts");
    expect(
      await screen.findByRole("heading", { name: "Change your temporary password" }),
    ).toBeInTheDocument();
  });

  it("cannot be skipped: every page leads to it", async () => {
    obliged();
    const { router } = renderApp("/accounts");
    expect(
      await screen.findByRole("heading", { name: "Turn on two-step verification" }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/two-factor-setup");
    expect(screen.getByText(/As an administrator you must turn on/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log out" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument(); // no way around it
  });

  it("shows the QR code and the key, confirms a code, then the recovery codes once", async () => {
    const { calls } = obliged();
    const { client, router } = renderApp("/two-factor-setup");
    await screen.findByRole("heading", { name: "Turn on two-step verification" });
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    click("Continue");
    expect(screen.getByText("Fill in this field.")).toBeInTheDocument();
    type("Password", "admin password 1");
    click("Continue");

    const qr = await screen.findByRole("img", { name: "QR code for the authenticator app" });
    expect(qr.tagName.toLowerCase()).toBe("svg");
    // The light margin of four modules around the 7 × 7 code.
    expect(qr.getAttribute("viewBox")).toBe("0 0 15 15");
    expect(screen.getByText(groupSecret(SECRET))).toBeInTheDocument();
    expect(cached(client)).not.toContain(SECRET);
    expect(cached(client)).not.toContain("admin password 1");

    type("6-digit code generated by the app", "12 34 56");
    click("Confirm and turn on");
    expect(await screen.findByRole("heading", { name: "Recovery codes" })).toHaveFocus();
    const list = screen.getByRole("list", { name: "Recovery codes" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(10);
    expect(screen.queryByText(groupSecret(SECRET))).not.toBeInTheDocument();
    expect(cached(client)).not.toContain(RECOVERY[0]);
    expect(cached(client)).not.toContain(SECRET);
    expect(router.state.location.pathname).toBe("/two-factor-setup"); // until they are saved

    click("I saved the codes");
    expect(await screen.findByRole("heading", { name: DASHBOARD })).toBeInTheDocument();
    expect(screen.queryByText(RECOVERY[0])).not.toBeInTheDocument();
    expect(cached(client)).not.toContain(RECOVERY[0]);
    expect(calls.filter((c) => c.method === "POST").map((c) => [c.path, c.body])).toEqual([
      ["/api/v1/me/two-factor/setup", { password: "admin password 1" }],
      ["/api/v1/me/two-factor/confirm", { code: "123456" }],
    ]);
  });

  it("starts again from the password when the secret was shown too long ago", async () => {
    obliged({
      "POST /api/v1/me/two-factor/confirm": {
        status: 409,
        body: { code: "TWO_FACTOR_NOT_STARTED", detail: "start the setup first" },
      },
    });
    renderApp("/two-factor-setup");
    await screen.findByRole("heading", { name: "Turn on two-step verification" });
    type("Password", "admin password 1");
    click("Continue");
    await screen.findByRole("img", { name: "QR code for the authenticator app" });
    type("6-digit code generated by the app", "123456");
    click("Confirm and turn on");
    expect(await screen.findByText("Too much time has passed: start again.")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("warns when the server's clock is far from this device's", async () => {
    obliged({
      "POST /api/v1/me/two-factor/setup": {
        body: {
          secret: SECRET,
          otpauth_uri: "otpauth://totp/x",
          qr: ["1"],
          digits: 6,
          period: 30,
          server_time: new Date(Date.now() - 95_000).toISOString(),
        },
      },
    });
    renderApp("/two-factor-setup");
    await screen.findByRole("heading", { name: "Turn on two-step verification" });
    type("Password", "admin password 1");
    click("Continue");
    expect(await screen.findByText(/differ by 95 seconds/)).toBeInTheDocument();
  });

  it("is shown in Italian too", async () => {
    obliged({}, { language: "it" });
    renderApp("/two-factor-setup");
    expect(
      await screen.findByRole("heading", { name: "Attiva la verifica in due passaggi" }),
    ).toBeInTheDocument();
  });

  it("sends a user who need not enrol to the dashboard", async () => {
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": { body: ME },
    });
    renderApp("/two-factor-setup");
    expect(await screen.findByRole("heading", { name: DASHBOARD })).toBeInTheDocument();
  });

  it("follows a 403 TWO_FACTOR_SETUP_REQUIRED during a session", async () => {
    let obligedNow = false;
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": () => ({ body: { ...ADMIN, two_factor_setup_required: obligedNow } }),
      "GET /api/v1/me/sessions": () => {
        obligedNow = true;
        return { status: 403, body: { code: "TWO_FACTOR_SETUP_REQUIRED", detail: "x" } };
      },
    });
    const { router } = renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });
    await act(() => api("GET", "/me/sessions").catch(() => undefined));
    await waitFor(() => expect(router.state.location.pathname).toBe("/two-factor-setup"));
  });
});

describe("helpers", () => {
  it("draw each run of dark modules once, inside the margin", () => {
    expect(qrPath(["0110", "1001"], 4)).toBe("M5 4h2v1h-2zM4 5h1v1h-1zM7 5h1v1h-1z");
    expect(qrPath(["000"], 4)).toBe("");
  });

  it("tell a code from a recovery code and group the key", () => {
    expect(secondFactor(" 123 456 ")).toEqual({ code: "123456" });
    expect(secondFactor(" Abcd-Efgh-Jkmn-Pqrs ")).toEqual({ recovery_code: "Abcd-Efgh-Jkmn-Pqrs" });
    expect(groupSecret("ABCDEFGHIJ")).toBe("ABCD EFGH IJ");
  });
});
