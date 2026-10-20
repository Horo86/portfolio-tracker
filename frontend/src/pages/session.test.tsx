import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { api } from "../api/client";
import { type Reply, stubFetch } from "../test/fetchStub";
import { HEALTH, ME, NO_ACCOUNTS, NO_SESSION, renderApp } from "../test/renderApp";
import { endSession, ME_KEY, startSession } from "../session";
import { returnPath } from "./Login";

const LUISA = { ...ME, id: "00000000-0000-4000-8000-000000000002", username: "luisa", display_name: "Luisa" };

function later() {
  let answer!: (reply: Reply) => void;
  const reply = new Promise<Reply>((resolve) => (answer = resolve));
  return { reply, answer };
}

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe("return to the page after login", () => {
  it("accepts only paths of this app", () => {
    expect(returnPath({ pathname: "/settings", search: "?account=a1" })).toBe("/settings?account=a1");
    expect(returnPath(undefined)).toBe("/");
    expect(returnPath({ pathname: "//evil.example", search: "" })).toBe("/");
    expect(returnPath({ pathname: "/\\evil.example", search: "" })).toBe("/");
    expect(returnPath({ pathname: "https://evil.example/", search: "" })).toBe("/");
    expect(returnPath({ pathname: "/login", search: "" })).toBe("/");
    expect(returnPath({ pathname: "/password-change", search: "" })).toBe("/");
  });

  function loginFlow() {
    let loggedIn = false;
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/setup/status": { body: { setup_required: false } },
      "GET /api/v1/me": () => (loggedIn ? { body: ME } : NO_SESSION),
      "POST /api/v1/auth/login": () => {
        loggedIn = true;
        return { body: { user: ME, token: null, expires_at: "2026-11-18T00:00:00Z" } };
      },
    });
  }

  async function logIn() {
    await screen.findByRole("heading", { name: "Log in" });
    type("Username", "mario");
    type("Password", "correct horse battery");
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
  }

  it("opens the page that asked for the login, with its query string", async () => {
    loginFlow();
    const { router } = renderApp("/about?account=a1");
    await logIn();
    expect(await screen.findByRole("heading", { name: "About" })).toBeInTheDocument();
    expect(router.state.location.pathname + router.state.location.search).toBe("/about?account=a1");
  });

  it("goes to the dashboard for a crafted address, without breaking the page", async () => {
    loginFlow();
    const { router } = renderApp("//evil.example");
    await logIn();
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
  });
});

describe("answers that arrive after the session changed", () => {
  it("a late 401 of the previous session does not log out the next user", async () => {
    const slow = later();
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/me": { body: ME },
      "GET /api/v1/me/sessions": () => slow.reply,
    });
    const { client } = renderApp("/");
    await screen.findByRole("heading", { name: "Dashboard" });

    const request = api("GET", "/me/sessions").catch(() => undefined);
    act(() => startSession(client, LUISA)); // another user logs in meanwhile
    slow.answer(NO_SESSION);
    await act(() => request);

    expect(client.getQueryData(ME_KEY)).toEqual(LUISA);
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(await screen.findByText("Luisa")).toBeInTheDocument();
  });

  it("a /me answer started before the logout does not bring the user back", async () => {
    const slow = later();
    let meCalls = 0;
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/setup/status": { body: { setup_required: false } },
      "GET /api/v1/me": () => {
        meCalls += 1;
        if (meCalls === 1) return { body: ME };
        if (meCalls === 2) return slow.reply; // the refetch in flight at the logout
        return NO_SESSION;
      },
    });
    const { client } = renderApp("/");
    await screen.findByRole("heading", { name: "Dashboard" });
    act(() => void client.invalidateQueries({ queryKey: ME_KEY }));
    await waitFor(() => expect(meCalls).toBe(2));

    act(() => endSession(client, "logout"));
    slow.answer({ body: ME });
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    await waitFor(() => expect(client.isFetching()).toBe(0));
    expect(client.getQueryData(ME_KEY)).toBeNull();
    expect(screen.getByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });
});

describe("passwords are not kept in the query cache", () => {
  it("not after a failed login", async () => {
    stubFetch({
      "GET /api/health": HEALTH,
      "GET /api/v1/me": NO_SESSION,
      "GET /api/v1/setup/status": { body: { setup_required: false } },
      "POST /api/v1/auth/login": { status: 401, body: { code: "INVALID_CREDENTIALS", detail: "x" } },
    });
    const { client } = renderApp("/login");
    await screen.findByRole("heading", { name: "Log in" });
    type("Username", "mario");
    type("Password", "a typed password");
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    await screen.findByText("Wrong username or password.");

    const cached = client.getMutationCache().getAll();
    expect(cached.every((m) => m.state.variables === undefined)).toBe(true);
    expect(JSON.stringify(cached.map((m) => m.state))).not.toContain("a typed password");
  });
});
