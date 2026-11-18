import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AdminUser, MarketDataStatus, SecuritySettings } from "../api/types";
import { type Handler, stubFetch } from "../test/fetchStub";
import { HEALTH, ME, NO_ACCOUNTS, renderApp } from "../test/renderApp";

// Invented users only (D-165: the Users screen of the Administrator).
const ADMIN = { ...ME, role: "ADMINISTRATOR" } as const;

const user = (id: string, username: string, fields: Partial<AdminUser> = {}): AdminUser => ({
  id: `00000000-0000-4000-8000-0000000000${id}`,
  username,
  first_name: "Example",
  last_name: "User",
  email: `${username}@example.org`,
  role: "INVESTOR",
  two_factor_enabled: false,
  must_change_password: false,
  disabled: false,
  created_at: "2026-10-01T09:00:00Z",
  last_login_at: "2026-10-20T12:00:00Z",
  ...fields,
});

const SELF = user("01", "mario", {
  id: ADMIN.id,
  role: "ADMINISTRATOR",
  first_name: "Mario",
  last_name: "Esempio",
});
const MARTA = user("02", "marta");
const TEMPORARY = "Wd7n-Qa3L-xP58-Mcz2";

const MARKET_STATUS: MarketDataStatus = {
  market_data: "ON",
  engines: [
    { name: "yahoo", checks: { OK: 3, NOT_FOUND: 1 } },
    { name: "openfigi", checks: { OK: 4 } },
  ],
  routes: { ETF_STOCK: ["yahoo"], BOND: [], FX: ["frankfurter"], CLASSIFY: ["openfigi", "yahoo"] },
};

const OWN_PASSWORD = "the administrator's own";

// The security settings as a new instance answers them: the server's configuration (D-180).
const SECURITY: SecuritySettings = {
  source: "environment",
  updated_at: null,
  settings: {
    session_idle_days: 7,
    session_absolute_days: 30,
    login_account_failures: 5,
    login_address_failures: 30,
    block_first_minutes: 1,
    block_max_minutes: 15,
  },
  bounds: {
    session_idle_days: { min: 1, max: 30 },
    session_absolute_days: { min: 1, max: 90 },
    login_account_failures: { min: 3, max: 20 },
    login_address_failures: { min: 10, max: 200 },
    block_first_minutes: { min: 1, max: 15 },
    block_max_minutes: { min: 15, max: 120 },
  },
};

function admin(
  extra: Record<string, Handler> = {},
  list: AdminUser[] = [SELF, MARTA],
  me: object = {},
) {
  let users = list;
  return {
    ...stubFetch({
      "GET /api/v1/me": { body: { ...ADMIN, ...me } },
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
      "GET /api/v1/admin/users": () => ({ body: users }),
      "GET /api/v1/admin/market-data/status": { body: MARKET_STATUS },
      "GET /api/v1/admin/security-settings": { body: SECURITY },
      ...extra,
    }),
    setUsers: (next: AdminUser[]) => {
      users = next;
    },
  };
}

const heading = () => screen.findByRole("heading", { name: "Administration", level: 1 });
const row = (name: string) =>
  screen.getByRole("rowheader", { name: new RegExp(`^${name}\\b`) }).closest("tr") as HTMLElement;
const click = (name: string | RegExp, within_: HTMLElement = document.body) =>
  fireEvent.click(within(within_).getByRole("button", { name }));
const change = (form: HTMLElement, label: string, value: string) =>
  fireEvent.change(within(form).getByLabelText(label), { target: { value } });

// The registration details of D-173 as typed, and as sent (normalised like `identity.py`).
const fillDetails = (form: HTMLElement) => {
  change(form, "First name", " Giulia  Maria ");
  change(form, "Last name", "D'Esempio");
  change(form, "E-mail", "Giulia@Example.ORG");
};
const SENT_DETAILS = { first_name: "Giulia Maria", last_name: "D'Esempio", email: "giulia@example.org" };

describe("administration: who sees it", () => {
  it("is not in the menu of an investor, and its address is not found", async () => {
    const { calls } = stubFetch({
      "GET /api/v1/me": { body: ME },
      "GET /api/health": HEALTH,
      "GET /api/v1/accounts": NO_ACCOUNTS,
    });
    renderApp("/admin");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Administration" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/v1/admin"))).toBe(false);
  });

  it("is in the menu of an administrator", async () => {
    admin();
    renderApp("/admin");
    await heading();
    expect(screen.getByRole("link", { name: "Administration" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

describe("administration: the users", () => {
  it("lists every user with role, status and last login; no action on one's own row", async () => {
    admin({}, [
      SELF,
      MARTA,
      user("03", "luca", { disabled: true, last_login_at: null }),
      user("04", "anna", { must_change_password: true }),
    ]);
    renderApp("/admin");
    await heading();
    await screen.findByRole("table", { name: "Users" });

    const marta = row("marta");
    expect(within(marta).getByText("Investor")).toBeInTheDocument();
    expect(within(marta).getByText("Active")).toBeInTheDocument();
    expect(within(marta).getByRole("button", { name: "Reset the password of marta" })).toBeVisible();
    expect(within(marta).getByRole("button", { name: "Disable marta" })).toBeVisible();
    expect(within(row("luca")).getByText("Disabled")).toBeInTheDocument();
    expect(within(row("luca")).getByText("Never")).toBeInTheDocument();
    expect(within(row("luca")).getByRole("button", { name: "Enable luca" })).toBeVisible();
    expect(within(row("anna")).getByText("Temporary password")).toBeInTheDocument();

    const self = row("mario");
    expect(within(self).getByText("Administrator")).toBeInTheDocument();
    expect(within(self).getByText("You")).toBeInTheDocument();
    expect(within(self).queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the load error with the administrator's hint", async () => {
    admin({
      "GET /api/v1/admin/users": { status: 500, body: { code: "INTERNAL_ERROR", detail: "x" } },
    });
    renderApp("/admin");
    await heading();
    const alert = (await screen.findByText("Something went wrong")).closest("[role=alert]");
    expect(alert).toHaveTextContent("container's logs");
    expect(alert).toHaveTextContent("INTERNAL_ERROR");
  });
});

describe("administration: a new user", () => {
  it("creates an investor by default and shows the temporary password once", async () => {
    const created = user("05", "giulia", { must_change_password: true, last_login_at: null });
    const { calls, setUsers } = admin({
      "POST /api/v1/admin/users": () => {
        setUsers([SELF, MARTA, created]);
        return { status: 201, body: { user: created, temporary_password: TEMPORARY } };
      },
    });
    const { client } = renderApp("/admin");
    await heading();
    await screen.findByRole("table", { name: "Users" });
    click("New user");
    const form = await screen.findByRole("dialog", { name: "New user" });
    expect(within(form).getByLabelText("Role")).toHaveValue("INVESTOR");

    click("Create user", form);
    for (const label of ["Username", "First name", "Last name", "E-mail"]) {
      expect(within(form).getByLabelText(label)).toHaveAccessibleDescription(
        expect.stringContaining("Fill in this field."),
      );
    }
    expect(within(form).getByLabelText("Username")).toHaveFocus();
    change(form, "Username", "a b");
    change(form, "First name", "Giulia2");
    change(form, "E-mail", "giulia@example");
    click("Create user", form);
    expect(within(form).getByLabelText("Username")).toHaveAccessibleDescription(
      expect.stringContaining("Use 3 to 64 characters"),
    );
    expect(within(form).getByLabelText("First name")).toHaveAccessibleDescription(
      expect.stringContaining("starting with a letter"),
    );
    expect(within(form).getByLabelText("E-mail")).toHaveAccessibleDescription(
      expect.stringContaining("name@example.org"),
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    change(form, "Username", " giulia ");
    fillDetails(form);
    click("Create user", form);

    const result = await screen.findByRole("dialog", { name: "User created" });
    expect(within(result).getByText(TEMPORARY)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      username: "giulia",
      ...SENT_DETAILS,
      role: "INVESTOR",
    });
    expect(JSON.stringify(client.getMutationCache().getAll().map((m) => m.state))).not.toContain(
      TEMPORARY,
    );

    click("Close", result);
    expect(screen.queryByText(TEMPORARY)).not.toBeInTheDocument();
    expect(await screen.findByText("User giulia created.")).toBeInTheDocument();
    expect(await screen.findByRole("rowheader", { name: /^giulia\b/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New user" })).toHaveFocus(); // back to the opener
  });

  // A new administrator can act on every user's access: the dialog asks who is acting, as the
  // reset does (D-174, D-175).
  const CREATED_ADMIN = {
    status: 201,
    body: {
      user: user("06", "paolo", { role: "ADMINISTRATOR", must_change_password: true }),
      temporary_password: TEMPORARY,
    },
  };
  const cached = (client: ReturnType<typeof renderApp>["client"]) =>
    JSON.stringify([
      client.getMutationCache().getAll().map((m) => [m.state, m.options.mutationKey]),
      client.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]),
    ]);
  const newAdministrator = async () => {
    await heading();
    await screen.findByRole("table", { name: "Users" });
    click("New user");
    const form = await screen.findByRole("dialog", { name: "New user" });
    fireEvent.change(within(form).getByLabelText("Username"), { target: { value: "paolo" } });
    fillDetails(form);
    return form;
  };
  const role = (form: HTMLElement, value: string) =>
    fireEvent.change(within(form).getByLabelText("Role"), { target: { value } });

  it("asks for the administrator's password only for a new administrator", async () => {
    const { calls } = admin({ "POST /api/v1/admin/users": CREATED_ADMIN });
    const { client } = renderApp("/admin");
    const form = await newAdministrator();
    expect(within(form).queryByLabelText("Your password")).not.toBeInTheDocument(); // an investor

    role(form, "ADMINISTRATOR");
    const own = within(form).getByLabelText("Your password");
    expect(own).toHaveAttribute("type", "password");
    expect(own).toHaveAttribute("autocomplete", "current-password");
    expect(within(form).queryByLabelText("Your code")).not.toBeInTheDocument(); // no 2FA

    click("Create user", form); // nothing typed: asked for, not sent
    expect(await within(form).findByText("Fill in this field.")).toBeInTheDocument();
    expect(own).toHaveFocus();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    change(form, "Your password", OWN_PASSWORD);
    click("Create user", form);

    const result = await screen.findByRole("dialog", { name: "User created" });
    expect(within(result).getByText(TEMPORARY)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      username: "paolo",
      ...SENT_DETAILS,
      role: "ADMINISTRATOR",
      password: OWN_PASSWORD,
    });
    expect(cached(client)).not.toContain(OWN_PASSWORD);
    expect(cached(client)).not.toContain(TEMPORARY);
  });

  it.each([
    ["the six digits of the app", " 123 456 ", { code: "123456" }],
    ["a recovery code", " tHq4-Rw8e-LmZ2-9Vcx ", { recovery_code: "tHq4-Rw8e-LmZ2-9Vcx" }],
  ])("with two-factor sign-in a new administrator also needs a code: %s", async (_name, typed, sent) => {
    const { calls } = admin({ "POST /api/v1/admin/users": CREATED_ADMIN }, undefined, {
      two_factor_enabled: true,
    });
    const { client } = renderApp("/admin");
    const form = await newAdministrator();
    role(form, "ADMINISTRATOR");
    const code = within(form).getByLabelText("Your code");
    expect(code).toHaveAttribute("autocomplete", "one-time-code");

    change(form, "Your password", OWN_PASSWORD);
    click("Create user", form); // the code is missing: asked for, not sent
    expect(await within(form).findByText("Fill in this field.")).toBeInTheDocument();
    expect(code).toHaveFocus();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    change(form, "Your code", typed);
    click("Create user", form);

    await screen.findByRole("dialog", { name: "User created" });
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      username: "paolo",
      ...SENT_DETAILS,
      role: "ADMINISTRATOR",
      password: OWN_PASSWORD,
      ...sent,
    });
    expect(cached(client)).not.toContain(Object.values(sent)[0]);
    expect(cached(client)).not.toContain(OWN_PASSWORD);
  });

  it("forgets the password and the code when the role goes back to investor", async () => {
    const { calls } = admin(
      {
        "POST /api/v1/admin/users": {
          status: 201,
          body: { user: user("06", "paolo", { must_change_password: true }), temporary_password: TEMPORARY },
        },
      },
      undefined,
      { two_factor_enabled: true },
    );
    renderApp("/admin");
    const form = await newAdministrator();
    role(form, "ADMINISTRATOR");
    change(form, "Your password", OWN_PASSWORD);
    change(form, "Your code", "123456");

    role(form, "INVESTOR");
    expect(within(form).queryByLabelText("Your password")).not.toBeInTheDocument();
    expect(within(form).queryByLabelText("Your code")).not.toBeInTheDocument();
    role(form, "ADMINISTRATOR");
    expect(within(form).getByLabelText("Your password")).toHaveValue("");
    expect(within(form).getByLabelText("Your code")).toHaveValue("");

    role(form, "INVESTOR");
    click("Create user", form);
    await screen.findByRole("dialog", { name: "User created" });
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      username: "paolo",
      ...SENT_DETAILS,
      role: "INVESTOR",
    });
  });

  it.each([
    ["CURRENT_PASSWORD_WRONG", 422, "The current password is wrong."],
    ["TWO_FACTOR_CODE_WRONG", 422, "The code is wrong, or it was already used. Wait for the next code and try again."],
    ["TOO_MANY_ATTEMPTS", 429, "Too many attempts."],
  ])("says why the server refused a new administrator (%s) and keeps the form", async (code, status, text) => {
    admin(
      { "POST /api/v1/admin/users": { status, body: { code, detail: "x", retry_after: 120 } } },
      undefined,
      { two_factor_enabled: true },
    );
    const { client } = renderApp("/admin");
    const form = await newAdministrator();
    role(form, "ADMINISTRATOR");
    change(form, "Your password", OWN_PASSWORD);
    change(form, "Your code", "123456");
    click("Create user", form);

    expect(await within(form).findByText(text, { exact: false })).toBeInTheDocument();
    expect(within(form).getByLabelText("Username")).toHaveValue("paolo");
    expect(within(form).getByLabelText("Your password")).toHaveValue(OWN_PASSWORD);
    expect(within(form).getByLabelText("Your code")).toHaveValue(""); // a code works once
    expect(client.getQueryData(["me"])).not.toBeNull(); // a 422 or 429: the session is not over
    expect(cached(client)).not.toContain(OWN_PASSWORD);
  });

  it("says when the username is taken and keeps the form", async () => {
    admin({
      "POST /api/v1/admin/users": {
        status: 409,
        body: { code: "USERNAME_TAKEN", detail: "x" },
      },
    });
    renderApp("/admin");
    await heading();
    await screen.findByRole("table", { name: "Users" });
    click("New user");
    const form = await screen.findByRole("dialog", { name: "New user" });
    fireEvent.change(within(form).getByLabelText("Username"), { target: { value: "marta" } });
    fillDetails(form);
    click("Create user", form);
    expect(await within(form).findByText("This username is already in use.")).toBeInTheDocument();
    expect(within(form).getByLabelText("Username")).toHaveValue("marta");
  });
});

describe("administration: reset, disable, enable", () => {
  const RESET = `POST /api/v1/admin/users/${MARTA.id}/reset-password`;
  const RESET_DONE = {
    body: { user: { ...MARTA, must_change_password: true }, temporary_password: TEMPORARY },
  };
  const openReset = async () => {
    await heading();
    await screen.findByRole("table", { name: "Users" });
    click("Reset the password of marta");
    return screen.findByRole("dialog", { name: "Reset the password of marta?" });
  };
  const cached = (client: ReturnType<typeof renderApp>["client"]) =>
    JSON.stringify([
      client.getMutationCache().getAll().map((m) => [m.state, m.options.mutationKey]),
      client.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]),
    ]);

  it("asks for the administrator's password, then shows the temporary password once", async () => {
    const { calls } = admin({ [RESET]: RESET_DONE });
    const { client } = renderApp("/admin");
    const confirm = await openReset();
    const own = within(confirm).getByLabelText("Your password");
    expect(own).toHaveAttribute("type", "password");
    expect(own).toHaveAttribute("autocomplete", "current-password");
    expect(within(confirm).queryByLabelText("Your code")).not.toBeInTheDocument(); // no 2FA

    click("Reset password", confirm); // nothing typed: asked for, not sent
    expect(await within(confirm).findByText("Fill in this field.")).toBeInTheDocument();
    expect(own).toHaveFocus();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    change(confirm, "Your password", OWN_PASSWORD);
    click("Reset password", confirm);

    const result = await screen.findByRole("dialog", { name: "Password reset" });
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ password: OWN_PASSWORD });
    expect(within(result).getByText(TEMPORARY)).toBeInTheDocument();
    expect(cached(client)).not.toContain(TEMPORARY);
    expect(cached(client)).not.toContain(OWN_PASSWORD); // nor what the administrator typed
    click("Close", result);
    expect(screen.queryByText(TEMPORARY)).not.toBeInTheDocument();
    expect(await screen.findByText("Password of marta reset.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset the password of marta" })).toHaveFocus();
  });

  it.each([
    ["the six digits of the app", " 123 456 ", { code: "123456" }],
    ["a recovery code", " tHq4-Rw8e-LmZ2-9Vcx ", { recovery_code: "tHq4-Rw8e-LmZ2-9Vcx" }],
  ])("with two-factor sign-in also asks for a code: %s", async (_name, typed, sent) => {
    const { calls } = admin({ [RESET]: RESET_DONE }, undefined, { two_factor_enabled: true });
    const { client } = renderApp("/admin");
    const confirm = await openReset();
    const code = within(confirm).getByLabelText("Your code");
    expect(code).toHaveAttribute("autocomplete", "one-time-code");

    change(confirm, "Your password", OWN_PASSWORD);
    click("Reset password", confirm); // the code is missing: asked for, not sent
    expect(await within(confirm).findByText("Fill in this field.")).toBeInTheDocument();
    expect(code).toHaveFocus();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    change(confirm, "Your code", typed);
    click("Reset password", confirm);

    await screen.findByRole("dialog", { name: "Password reset" });
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ password: OWN_PASSWORD, ...sent });
    expect(cached(client)).not.toContain(Object.values(sent)[0]);
    expect(cached(client)).not.toContain(OWN_PASSWORD);
  });

  it.each([
    ["CURRENT_PASSWORD_WRONG", "The current password is wrong."],
    ["TWO_FACTOR_CODE_WRONG", "The code is wrong, or it was already used. Wait for the next code and try again."],
    ["TWO_FACTOR_CODE_REQUIRED", "Enter a code from your authenticator app, or a recovery code."],
  ])("says why the server refused (%s) and keeps the dialog open", async (code, text) => {
    admin({ [RESET]: { status: 422, body: { code, detail: "x" } } }, undefined, {
      two_factor_enabled: true,
    });
    const { client } = renderApp("/admin");
    const confirm = await openReset();
    change(confirm, "Your password", OWN_PASSWORD);
    change(confirm, "Your code", "654321");

    click("Reset password", confirm);

    expect(await within(confirm).findByText(text)).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Password reset" })).not.toBeInTheDocument();
    expect(cached(client)).not.toContain(OWN_PASSWORD);
    expect(cached(client)).not.toContain("654321");
  });

  it("translates the refusal on one's own row (never offered there, 409 from the server)", async () => {
    admin({ [RESET]: { status: 409, body: { code: "CANNOT_RESET_OWN_PASSWORD", detail: "x" } } });
    renderApp("/admin");
    const confirm = await openReset();
    change(confirm, "Your password", OWN_PASSWORD);

    click("Reset password", confirm);

    expect(
      await within(confirm).findByText("Change your own password from Settings."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Password reset" })).not.toBeInTheDocument();
  });

  it("cancels a reset without asking the server", async () => {
    const { calls } = admin();
    renderApp("/admin");
    await heading();
    await screen.findByRole("table", { name: "Users" });
    click("Reset the password of marta");
    click("Cancel", await screen.findByRole("dialog", { name: "Reset the password of marta?" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("disables after a confirmation, then enables again", async () => {
    const { setUsers } = admin({
      [`POST /api/v1/admin/users/${MARTA.id}/disable`]: () => {
        setUsers([SELF, { ...MARTA, disabled: true }]);
        return { body: { ...MARTA, disabled: true } };
      },
      [`POST /api/v1/admin/users/${MARTA.id}/enable`]: () => {
        setUsers([SELF, MARTA]);
        return { body: MARTA };
      },
    });
    renderApp("/admin");
    await heading();
    await screen.findByRole("table", { name: "Users" });
    click("Disable marta");
    click("Disable", await screen.findByRole("dialog", { name: "Disable marta?" }));

    expect(await screen.findByText("User marta disabled.")).toBeInTheDocument();
    await waitFor(() => expect(within(row("marta")).getByText("Disabled")).toBeInTheDocument());
    // The row's button became "Enable": what opened the dialog is gone.
    expect(screen.getByRole("heading", { name: "Administration", level: 1 })).toHaveFocus();

    click("Enable marta");
    expect(await screen.findByText("User marta enabled again.")).toBeInTheDocument();
    await waitFor(() => expect(within(row("marta")).getByText("Active")).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Administration", level: 1 })).toHaveFocus();
  });

  it("explains why the last administrator cannot be disabled", async () => {
    const other = user("07", "chiara", { role: "ADMINISTRATOR" });
    admin(
      {
        [`POST /api/v1/admin/users/${other.id}/disable`]: {
          status: 409,
          body: { code: "LAST_ADMINISTRATOR", detail: "x" },
        },
      },
      [SELF, other],
    );
    renderApp("/admin");
    await heading();
    await screen.findByRole("table", { name: "Users" });
    click("Disable chiara");
    const dialog = await screen.findByRole("dialog", { name: "Disable chiara?" });
    click("Disable", dialog);
    expect(
      await within(dialog).findByText("The last active administrator cannot be disabled."),
    ).toBeInTheDocument();
  });
});

describe("the notice after an administrator reset", () => {
  const forced = (fields: object) =>
    stubFetch({
      "GET /api/v1/me": { body: { ...ME, must_change_password: true, ...fields } },
      "GET /api/health": HEALTH,
    });

  it("tells the user, with the date, on the password change", async () => {
    forced({ password_reset_at: "2026-10-30T09:15:00Z" });
    renderApp("/password-change");
    await screen.findByRole("heading", { name: "Change your temporary password" });
    expect(
      screen.getByText("Your password was reset by the administrator on Oct 30, 2026."),
    ).toBeInTheDocument();
  });

  it("says nothing to a new user", async () => {
    forced({ password_reset_at: null });
    renderApp("/password-change");
    await screen.findByRole("heading", { name: "Change your temporary password" });
    expect(screen.queryByText(/reset by the administrator/)).not.toBeInTheDocument();
  });
});

describe("the price sources card (D-175 (4))", () => {
  const card = async () =>
    (await screen.findByRole("heading", { name: "Price sources", level: 2 })).closest(
      "section",
    ) as HTMLElement;
  const sources = (section: HTMLElement, role: string) =>
    within(section).getByText(role).closest(".row") as HTMLElement;

  it("shows the enabled engines of each role in order, read only", async () => {
    admin();
    renderApp("/admin");
    const section = await card();
    expect(await within(section).findByText(/Automatic prices are on/)).toBeInTheDocument();
    const classify = within(sources(section, "Security type")).getAllByRole("listitem");
    expect(classify.map((item) => item.textContent)).toEqual(["openfigi (4 found)", "yahoo (3 found, 1 not found)"]);
    expect(within(sources(section, "Exchange rates")).getByRole("listitem")).toHaveTextContent(
      "frankfurter",
    );
    expect(
      within(sources(section, "Bonds")).getByText(
        "Bonds have no automatic source yet; use manual prices.",
      ),
    ).toBeInTheDocument();
    // Nothing to change here: the full screen is 1.1.0.
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
    expect(within(section).queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("says when automatic prices are off, and a role without engines", async () => {
    admin({
      "GET /api/v1/admin/market-data/status": {
        body: { ...MARKET_STATUS, market_data: "OFF", routes: { ...MARKET_STATUS.routes, FX: [] } },
      },
    });
    renderApp("/admin");
    const section = await card();
    expect(
      await within(section).findByText(
        "Automatic prices are off on this installation: prices are entered by hand.",
      ),
    ).toBeInTheDocument();
    expect(within(sources(section, "Exchange rates")).getByText("None")).toBeInTheDocument();
  });
});


describe("the security card (D-182)", () => {
  const SAVE_PATH = "PUT /api/v1/admin/security-settings";
  const card = async () => {
    const section = (
      await screen.findByRole("heading", { name: "Security", level: 2 })
    ).closest("section") as HTMLElement;
    await within(section).findByRole("group", { name: "Sessions" });
    return section;
  };
  const cached = (client: ReturnType<typeof renderApp>["client"]) =>
    JSON.stringify([
      client.getMutationCache().getAll().map((m) => [m.state, m.options.mutationKey]),
      client.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]),
    ]);
  const saved = (changes: Partial<SecuritySettings["settings"]>, shortened = 0) => ({
    body: {
      ...SECURITY,
      source: "database",
      updated_at: "2026-11-02T10:00:00Z",
      settings: { ...SECURITY.settings, ...changes },
      sessions_shortened: shortened,
    },
  });

  it("shows where the values come from, the two groups and each hint", async () => {
    admin();
    renderApp("/admin");
    const section = await card();
    expect(
      within(section).getByText(
        "Initial values, from the server's configuration. From the first save, the values saved here apply.",
      ),
    ).toBeInTheDocument();
    expect(within(section).getByRole("group", { name: "Failed logins" })).toBeInTheDocument();
    const idle = within(section).getByLabelText("Days without use");
    expect(idle).toHaveValue("7");
    expect(idle).toHaveAttribute("inputmode", "numeric");
    expect(idle).toHaveAccessibleDescription(
      "1 to 30. A session not used for this long ends. Applies at once, also to open sessions.",
    );
    expect(within(section).getByLabelText("Longest block (minutes)")).toHaveValue("15");
  });

  it("says when the values were saved", async () => {
    admin({ "GET /api/v1/admin/security-settings": saved({}) });
    renderApp("/admin");
    const section = await card();
    expect(within(section).getByText(/^Saved on .*2026\.$/)).toBeInTheDocument();
  });

  it("shows the loading state, then the load error with a retry", async () => {
    let fail = true;
    admin({
      "GET /api/v1/admin/security-settings": () =>
        fail ? { status: 500, body: { code: "SERVER", detail: "x" } } : { body: SECURITY },
    });
    renderApp("/admin");
    const heading_ = await screen.findByRole("heading", { name: "Security", level: 2 });
    const section = heading_.closest("section") as HTMLElement;
    const retry = await within(section).findByRole("button", { name: /try again/i });
    fail = false;
    fireEvent.click(retry);
    expect(await within(section).findByLabelText("Days without use")).toHaveValue("7");
  });

  it("keeps Save inactive until a value differs", async () => {
    const { calls } = admin();
    renderApp("/admin");
    const section = await card();
    const save = within(section).getByRole("button", { name: "Save" });
    expect(save).toHaveAttribute("aria-disabled", "true");
    click("Save", section);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    change(section, "Days without use", "8");
    expect(save).toHaveAttribute("aria-disabled", "false");
    change(section, "Days without use", "7");
    expect(save).toHaveAttribute("aria-disabled", "true");
  });

  it.each([
    ["Days without use", "seven", "Enter a whole number."],
    ["Days without use", "7.5", "Enter a whole number."],
    ["Days after login", "91", "Enter a number from 1 to 90."],
    ["Attempts per user before a block", "2", "Enter a number from 3 to 20."],
    ["First block (minutes)", "16", "Enter a number from 1 to 15."],
    ["Longest block (minutes)", "14", "Enter a number from 15 to 120."],
    ["Attempts per address before a block", "201", "Enter a number from 10 to 200."],
  ])("refuses %s = %s before asking anything", async (label, typed, message) => {
    const { calls } = admin();
    renderApp("/admin");
    const section = await card();
    change(section, label, typed);
    click("Save", section);
    const field = within(section).getByLabelText(label);
    expect(await within(section).findByText(message)).toBeInTheDocument();
    expect(field).toHaveFocus();
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("checks the two cross rules and focuses the first wrong field", async () => {
    admin();
    renderApp("/admin");
    const section = await card();
    change(section, "Days without use", "20");
    change(section, "Days after login", "10");
    change(section, "Attempts per user before a block", "8");
    change(section, "Attempts per address before a block", "15");
    click("Save", section);
    expect(
      await within(section).findByText("It cannot be more than the days after login (10)."),
    ).toBeInTheDocument();
    expect(
      within(section).getByText("It must be at least twice the attempts per user (at least 16)."),
    ).toBeInTheDocument();
    expect(within(section).getByLabelText("Days without use")).toHaveFocus();
  });

  it("lists the changes, asks the password, saves and says so", async () => {
    const { calls } = admin({ [SAVE_PATH]: saved({ session_idle_days: 8 }) });
    const { client } = renderApp("/admin");
    const section = await card();
    change(section, "Days without use", " 8 ");
    click("Save", section);

    const dialog = await screen.findByRole("dialog", { name: "Save the security settings?" });
    expect(within(dialog).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Days without use: from 7 to 8",
    ]);
    expect(within(dialog).queryByLabelText("Your code")).not.toBeInTheDocument(); // no 2FA
    change(dialog, "Your password", OWN_PASSWORD);
    click("Save", dialog);

    expect(await screen.findByText("Security settings saved.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      ...SECURITY.settings,
      session_idle_days: 8,
      password: OWN_PASSWORD,
    });
    expect(within(section).getByRole("button", { name: "Save" })).toHaveFocus();
    expect(within(section).getByLabelText("Days without use")).toHaveValue("8");
    expect(within(section).getByText(/^Saved on /)).toBeInTheDocument();
    expect(cached(client)).not.toContain(OWN_PASSWORD);
  });

  it("says how many open sessions were shortened", async () => {
    admin({ [SAVE_PATH]: saved({ session_absolute_days: 10 }, 3) });
    renderApp("/admin");
    const section = await card();
    change(section, "Days after login", "10");
    click("Save", section);
    const dialog = await screen.findByRole("dialog", { name: "Save the security settings?" });
    change(dialog, "Your password", OWN_PASSWORD);
    click("Save", dialog);
    expect(
      await screen.findByText("Security settings saved. Open sessions shortened: 3."),
    ).toBeInTheDocument();
  });

  it("with two-factor sign-in also asks for a code, never kept in a cache", async () => {
    const { calls } = admin({ [SAVE_PATH]: saved({ block_max_minutes: 60 }) }, undefined, {
      two_factor_enabled: true,
    });
    const { client } = renderApp("/admin");
    const section = await card();
    change(section, "Longest block (minutes)", "60");
    click("Save", section);
    const dialog = await screen.findByRole("dialog", { name: "Save the security settings?" });
    change(dialog, "Your password", OWN_PASSWORD);
    change(dialog, "Your code", "123 456");
    click("Save", dialog);

    await screen.findByText("Security settings saved.");
    expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({
      block_max_minutes: 60,
      password: OWN_PASSWORD,
      code: "123456",
    });
    expect(cached(client)).not.toContain(OWN_PASSWORD);
    expect(cached(client)).not.toContain("123456");
  });

  it.each([
    ["CURRENT_PASSWORD_WRONG", "The current password is wrong."],
    ["SECURITY_SETTINGS_INVALID", "Some values are not allowed: check the fields."],
  ])("keeps the dialog open with the server's refusal (%s)", async (code, text) => {
    admin({ [SAVE_PATH]: { status: 422, body: { code, detail: "x" } } });
    const { client } = renderApp("/admin");
    const section = await card();
    change(section, "Days without use", "6");
    click("Save", section);
    const dialog = await screen.findByRole("dialog", { name: "Save the security settings?" });
    change(dialog, "Your password", OWN_PASSWORD);
    click("Save", dialog);
    expect(await within(dialog).findByText(text)).toBeInTheDocument();
    expect(within(section).getByLabelText("Days without use")).toHaveValue("6");
    expect(cached(client)).not.toContain(OWN_PASSWORD);
  });
});
