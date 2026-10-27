import { act, fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { api, CSRF_HEADER, CSRF_VALUE } from "../api/client";
import { stubFetch } from "../test/fetchStub";
import { DASHBOARD, HEALTH, ME, NO_ACCOUNTS, NO_SESSION, renderApp } from "../test/renderApp";

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

describe("session guard", () => {
  it("sends a fresh instance to the first run, with restore disabled", async () => {
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": NO_SESSION,
      "GET /api/v1/setup/status": { body: { setup_required: true } },
    });
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Welcome" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore from a backup" })).toBeDisabled();
    expect(screen.getByText(/available in a later version/)).toBeInTheDocument();
  });

  it("sends a visitor without a session to the login", async () => {
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": NO_SESSION,
      "GET /api/v1/setup/status": { body: { setup_required: false } },
    });
    renderApp("/some/page");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("applies the user's language and theme from /me, which win over the browser", async () => {
    window.localStorage.setItem("pt.language", "en");
    window.localStorage.setItem("pt.theme", "sobrio");
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": { body: { ...ME, language: "it", theme: "grafite" } },
    });
    renderApp("/");
    expect(
      await screen.findByRole("heading", { name: "Ciao Mario, ecco i tuoi investimenti" }),
    ).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("grafite");
    expect(document.documentElement.lang).toBe("it");
    expect(window.localStorage.getItem("pt.language")).toBe("it");
    expect(window.localStorage.getItem("pt.theme")).toBe("grafite");
  });

  it("returns to the login when a call answers 401 during the session", async () => {
    // The session ends on the server (expired, revoked): every call answers 401 from then on.
    let ended = false;
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": () => (ended ? NO_SESSION : { body: ME }),
      "GET /api/v1/me/sessions": () => {
        ended = true;
        return NO_SESSION;
      },
      "GET /api/v1/setup/status": { body: { setup_required: false } },
    });
    const { router } = renderApp("/");
    await screen.findByRole("heading", { name: DASHBOARD });
    await act(() => api("GET", "/me/sessions").catch(() => undefined));
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    // An expired session keeps the page to return to after the login.
    expect(router.state.location.state).toMatchObject({ from: { pathname: "/" } });
  });
});

describe("login", () => {
  function loggedOut(login: Parameters<typeof stubFetch>[0][string]) {
    return stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": NO_SESSION,
      "GET /api/v1/setup/status": { body: { setup_required: false } },
      "POST /api/v1/auth/login": login,
    });
  }

  it("asks for the missing fields before sending anything", async () => {
    const { calls } = loggedOut({ body: {} });
    renderApp("/login");
    await screen.findByRole("heading", { name: "Log in" });
    click("Log in");
    expect(screen.getAllByText("Fill in this field.")).toHaveLength(2);
    expect(screen.getByLabelText("Username")).toHaveFocus();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows the translated error, never the server detail, and the wait after 429", async () => {
    let attempts = 0;
    loggedOut(() =>
      ++attempts < 2
        ? { status: 401, body: { code: "INVALID_CREDENTIALS", detail: "wrong username or password" } }
        : {
            status: 429,
            body: { code: "TOO_MANY_ATTEMPTS", detail: "too many failed logins", retry_after: 90 },
            headers: { "Retry-After": "90" },
          },
    );
    renderApp("/login");
    await screen.findByRole("heading", { name: "Log in" });
    type("Username", "mario");
    type("Password", "not-the-password");
    click("Log in");
    expect(await screen.findByText("Wrong username or password.")).toHaveAttribute("role", "alert");
    expect(screen.queryByText(/wrong username or password$/)).toBeNull();
    expect(screen.getByLabelText("Password")).toHaveValue("");

    type("Password", "still-not-it!");
    click("Log in");
    expect(await screen.findByText("Too many attempts. Try again in 2 minutes.")).toBeInTheDocument();
  });

  it("logs in with the CSRF header and opens the page", async () => {
    const { calls } = loggedOut({ body: { user: ME, token: null, expires_at: "2026-11-18T00:00:00Z" } });
    renderApp("/login");
    await screen.findByRole("heading", { name: "Log in" });
    type("Username", "mario");
    type("Password", "correct horse battery");
    click("Log in");
    expect(await screen.findByRole("heading", { name: DASHBOARD })).toBeInTheDocument();
    const login = calls.find((c) => c.method === "POST");
    expect(login?.body).toEqual({ username: "mario", password: "correct horse battery" });
    expect(login?.headers.get(CSRF_HEADER)).toBe(CSRF_VALUE);
  });

  it("leaves the login page when a session already exists", async () => {
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": { body: ME },
      "GET /api/v1/setup/status": { body: { setup_required: false } },
    });
    renderApp("/login");
    expect(await screen.findByRole("heading", { name: DASHBOARD })).toBeInTheDocument();
  });
});

describe("first run", () => {
  function freshInstance(create: Parameters<typeof stubFetch>[0][string]) {
    return stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": NO_SESSION,
      "GET /api/v1/setup/status": { body: { setup_required: true } },
      "POST /api/v1/setup/admin": create,
    });
  }

  it("checks the administrator form, then creates it in the chosen language", async () => {
    const { calls } = freshInstance(({ body }) => ({
      status: 201,
      body: { user: { ...ME, username: "admin", role: "ADMINISTRATOR", ...(body as object) } },
    }));
    renderApp("/welcome");
    fireEvent.click(await screen.findByRole("button", { name: "Start from scratch" }));
    await screen.findByRole("heading", { name: "Create the administrator" });

    type("Username", "a");
    type("Password", "short");
    click("Continue");
    expect(screen.getByText(/Use 3 to 32 characters/)).toBeInTheDocument();
    expect(screen.getByText("The password must be at least 12 characters long.")).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toHaveFocus();

    type("Username", "admin");
    type("Password", "a long enough password");
    type("Repeat the password", "a different password");
    click("Continue");
    expect(screen.getByText("The two passwords do not match.")).toBeInTheDocument();
    expect(screen.getByLabelText("Repeat the password")).toHaveAttribute("aria-invalid", "true");

    type("Repeat the password", "a long enough password");
    click("Continue");
    await screen.findByRole("heading", { name: "Choose the language" });
    fireEvent.click(screen.getByLabelText("Italiano"));
    // The page switches once the Italian texts are there.
    fireEvent.click(await screen.findByRole("button", { name: "Crea l'amministratore" }));

    expect(await screen.findByRole("heading", { name: DASHBOARD })).toBeInTheDocument();
    const create = calls.find((c) => c.method === "POST");
    expect(create?.body).toEqual({
      username: "admin",
      password: "a long enough password",
      language: "it",
    });
    expect(document.documentElement.lang).toBe("it");
  });

  it("goes to the login when another browser finished the setup meanwhile", async () => {
    freshInstance({ status: 409, body: { code: "SETUP_ALREADY_DONE", detail: "x" } });
    renderApp("/welcome");
    fireEvent.click(await screen.findByRole("button", { name: "Start from scratch" }));
    type("Username", "admin");
    type("Password", "a long enough password");
    type("Repeat the password", "a long enough password");
    click("Continue");
    fireEvent.click(await screen.findByRole("button", { name: "Create the administrator" }));
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("already set up");
  });
});

describe("forced password change", () => {
  it("replaces the temporary password before anything else", async () => {
    let attempts = 0;
    const { calls } = stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": { body: { ...ME, must_change_password: true } },
      "POST /api/v1/me/password": () =>
        ++attempts === 1
          ? { status: 422, body: { code: "CURRENT_PASSWORD_WRONG", detail: "x" } }
          : { body: { ...ME, must_change_password: false } },
    });
    renderApp("/");
    await screen.findByRole("heading", { name: "Change your temporary password" });

    type("Temporary password", "Ab12-Cd34-Ef56-Gh78");
    type("New password", "too short");
    click("Change password");
    expect(screen.getByText("The password must be at least 12 characters long.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    type("New password", "my own new password");
    type("Repeat the new password", "my own new password");
    click("Change password");
    expect(await screen.findByText("The current password is wrong.")).toBeInTheDocument();

    click("Change password");
    expect(await screen.findByRole("heading", { name: DASHBOARD })).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST").at(-1)?.body).toEqual({
      current_password: "Ab12-Cd34-Ef56-Gh78",
      new_password: "my own new password",
    });
  });
});
