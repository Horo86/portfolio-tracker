import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AdminUser } from "../api/types";
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

const OWN_PASSWORD = "the administrator's own";

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

  it("creates an administrator when asked", async () => {
    const created = user("06", "paolo", { role: "ADMINISTRATOR", must_change_password: true });
    const { calls } = admin({
      "POST /api/v1/admin/users": {
        status: 201,
        body: { user: created, temporary_password: TEMPORARY },
      },
    });
    renderApp("/admin");
    await heading();
    await screen.findByRole("table", { name: "Users" });
    click("New user");
    const form = await screen.findByRole("dialog", { name: "New user" });
    fireEvent.change(within(form).getByLabelText("Username"), { target: { value: "paolo" } });
    fillDetails(form);
    fireEvent.change(within(form).getByLabelText("Role"), { target: { value: "ADMINISTRATOR" } });
    click("Create user", form);
    await screen.findByRole("dialog", { name: "User created" });
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      username: "paolo",
      ...SENT_DETAILS,
      role: "ADMINISTRATOR",
    });
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
