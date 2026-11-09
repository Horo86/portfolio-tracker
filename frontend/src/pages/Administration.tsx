import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { AdminUser, Role, TemporaryPasswordOut } from "../api/types";
import { Dialog } from "../components/Dialog";
import { Field, SelectField } from "../components/Field";
import { FormError } from "../components/FormError";
import { Notice, useNotice } from "../components/Notice";
import { focusPageTitle, PageTitle } from "../components/PageTitle";
import { LoadError, Loading } from "../components/Status";
import { formatMoment } from "../format/date";
import { useMe } from "../session";
import {
  emailProblem,
  nameProblem,
  normalEmail,
  normalName,
  normalUsername,
  usernameProblem,
} from "../validation";
import NotFound from "./NotFound";

const USERS_KEY = ["admin-users"] as const;

type Open =
  | { kind: "new" }
  | { kind: "reset" | "disable"; user: AdminUser }
  | { kind: "password"; heading: string; text: string; password: string; notice: string }
  | null;

/**
 * Mockup 17, the Users block alone (D-165): the users with role, status and last login;
 * create one, reset a password, disable and enable. No role change, no delete, and nothing of
 * other users' accounts. A non-administrator gets "not found", as from the API (D-92).
 */
export default function Administration() {
  const me = useMe().data;
  if (me?.role !== "ADMINISTRATOR") return <NotFound />;
  return <Users selfId={me.id} />;
}

function Users({ selfId }: { selfId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const headingId = useId();
  const users = useQuery({
    queryKey: USERS_KEY,
    queryFn: ({ signal }) => api<AdminUser[]>("GET", "/admin/users", undefined, signal),
  });
  const [open, setOpen] = useState<Open>(null);
  const { notice, announce } = useNotice();
  // The button that started the current flow (a confirmation may be followed by the password
  // dialog, so the dialog's own "back to the opener" is not enough).
  // "title": the button is replaced by the change itself (a disabled user's "Enable").
  const opener = useRef<HTMLElement | "title" | null>(null);
  const start = (from: HTMLElement, next: Open) => {
    opener.current = from;
    setOpen(next);
  };

  // When the flow ends the focus goes back to its button; if that is gone or about to be
  // replaced, to the page's heading. The live region says what happened.
  useEffect(() => {
    if (open !== null || opener.current === null) return;
    const from = opener.current;
    opener.current = null;
    if (from !== "title" && from.isConnected) from.focus();
    else focusPageTitle();
  }, [open]);

  // The server's answer goes into the list at once (the row's status and buttons change in
  // the same render), then the list is read again.
  const refresh = (changed?: AdminUser) => {
    if (changed) {
      client.setQueryData<AdminUser[]>(USERS_KEY, (list) =>
        list?.map((u) => (u.id === changed.id ? changed : u)),
      );
    }
    void client.invalidateQueries({ queryKey: USERS_KEY });
  };
  const enable = useMutation({
    mutationFn: (user: AdminUser) => api<AdminUser>("POST", `/admin/users/${user.id}/enable`),
    onSuccess: (user) => {
      refresh(user);
      focusPageTitle(); // the row's "Enable" is replaced by "Disable"
      announce(t("admin.enabledNotice", { name: user.username }));
    },
  });
  const showPassword = (heading: string, text: string, out: TemporaryPasswordOut, said: string) =>
    setOpen({ kind: "password", heading, text, password: out.temporary_password, notice: said });

  const locale = i18n.language;
  const list = users.data;

  return (
    <>
      <div className="page-head">
        <div>
          <PageTitle title={t("admin.heading")} />
          <p className="muted">{t("admin.intro")}</p>
        </div>
      </div>
      <Notice notice={notice} />
      <FormError error={enable.error} />

      <section className="card" aria-labelledby={headingId}>
        <h2 id={headingId} className="card-label">
          {t("admin.users")}
        </h2>
        {users.isPending && <Loading />}
        {users.isError && (
          <LoadError error={users.error} onRetry={() => void users.refetch()} level={3} />
        )}
        {list && (
          <div className="table-scroll">
            <table className="data users-table" aria-labelledby={headingId}>
              <thead>
                <tr>
                  <th scope="col">{t("admin.user")}</th>
                  <th scope="col">{t("admin.email")}</th>
                  <th scope="col">{t("admin.role")}</th>
                  <th scope="col">{t("admin.status")}</th>
                  <th scope="col">{t("admin.lastLogin")}</th>
                  <th scope="col">
                    <span className="visually-hidden">{t("admin.actions")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((user) => {
                  const self = user.id === selfId;
                  const name = user.username;
                  return (
                    <tr key={user.id}>
                      <th scope="row">
                        <span className="user-cell">
                          {name}{" "}
                          <span className="muted small">
                            {user.first_name} {user.last_name}
                          </span>
                        </span>
                      </th>
                      <td className="user-email">{user.email}</td>
                      <td>
                        <span className="pill pill-soft">{t(`roles.${user.role}`)}</span>
                      </td>
                      <td>
                        <span className="user-status">
                          {t(user.disabled ? "admin.disabled" : "admin.active")}
                          {user.must_change_password && !user.disabled && (
                            <span className="pill pill-warning">{t("admin.temporary")}</span>
                          )}
                        </span>
                      </td>
                      <td className="nowrap">
                        {user.last_login_at
                          ? formatMoment(user.last_login_at, locale)
                          : t("admin.never")}
                      </td>
                      <td>
                        {self ? (
                          // D-165: no action on the Administrator's own row.
                          <span className="pill pill-soft">{t("admin.you")}</span>
                        ) : (
                          <div className="row-actions">
                            <button
                              type="button"
                              className="link-button"
                              aria-label={t("admin.resetRow", { name })}
                              onClick={(e) => start(e.currentTarget, { kind: "reset", user })}
                            >
                              {t("admin.reset")}
                            </button>
                            {user.disabled ? (
                              <button
                                type="button"
                                className="link-button"
                                aria-label={t("admin.enableRow", { name })}
                                disabled={enable.isPending}
                                onClick={() => enable.mutate(user)}
                              >
                                {t("admin.enable")}
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="link-button danger-link"
                                aria-label={t("admin.disableRow", { name })}
                                onClick={(e) => start(e.currentTarget, { kind: "disable", user })}
                              >
                                {t("admin.disable")}
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {list && (
          <div className="actions actions-start">
            <button
              type="button"
              className="button button-primary"
              onClick={(e) => start(e.currentTarget, { kind: "new" })}
            >
              {t("admin.newUser")}
            </button>
          </div>
        )}
      </section>

      {open?.kind === "new" && (
        <NewUserDialog
          onClose={() => setOpen(null)}
          onCreated={(out) => {
            refresh();
            const name = out.user.username;
            showPassword(
              t("admin.createdHeading"),
              t("admin.createdText", { name }),
              out,
              t("admin.createdNotice", { name }),
            );
          }}
        />
      )}
      {open?.kind === "reset" && (
        <ConfirmDialog
          heading={t("admin.resetHeading", { name: open.user.username })}
          text={t("admin.resetText", { name: open.user.username })}
          submit={t("admin.reset")}
          path={`/admin/users/${open.user.id}/reset-password`}
          onClose={() => setOpen(null)}
          onDone={(out: TemporaryPasswordOut) => {
            refresh(out.user);
            const name = out.user.username;
            showPassword(
              t("admin.resetDoneHeading"),
              t("admin.resetDoneText", { name }),
              out,
              t("admin.resetNotice", { name }),
            );
          }}
        />
      )}
      {open?.kind === "disable" && (
        <ConfirmDialog
          heading={t("admin.disableHeading", { name: open.user.username })}
          text={t("admin.disableText", { name: open.user.username })}
          submit={t("admin.disable")}
          path={`/admin/users/${open.user.id}/disable`}
          onClose={() => setOpen(null)}
          onDone={(user: AdminUser) => {
            refresh(user);
            opener.current = "title";
            setOpen(null);
            announce(t("admin.disabledNotice", { name: user.username }));
          }}
        />
      )}
      {open?.kind === "password" && (
        <PasswordDialog
          heading={open.heading}
          text={open.text}
          password={open.password}
          onClose={() => {
            const said = open.notice;
            setOpen(null); // the password leaves the page's state with the dialog
            announce(said);
          }}
        />
      )}
    </>
  );
}

/**
 * A request whose answer may carry a temporary password: the answer goes straight to `onDone`
 * and the mutation itself resolves to nothing, so the password lives only in the screen's own
 * state, never in the mutation cache (D-78).
 */
function useOneShot<T>(send: () => Promise<T>, onDone: (out: T) => void) {
  const mutation = useMutation({
    mutationFn: async () => {
      onDone(await send());
    },
    gcTime: 0,
  });
  return { run: () => mutation.mutate(), isPending: mutation.isPending, error: mutation.error };
}

function NewUserDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (out: TemporaryPasswordOut) => void;
}) {
  const { t } = useTranslation();
  const [values, setValues] = useState({ username: "", firstName: "", lastName: "", email: "" });
  const [role, setRole] = useState<Role>("INVESTOR");
  const [problems, setProblems] = useState<Record<string, string>>({});
  const usernameRef = useRef<HTMLInputElement>(null);
  const firstNameRef = useRef<HTMLInputElement>(null);
  const lastNameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  // Sent as the API stores them (D-173): the same normalisation as `identity.py`.
  const body = () => ({
    username: normalUsername(values.username),
    first_name: normalName(values.firstName),
    last_name: normalName(values.lastName),
    email: normalEmail(values.email),
    role,
  });
  const create = useOneShot(
    () => api<TemporaryPasswordOut>("POST", "/admin/users", body()),
    onCreated,
  );
  const set = (name: keyof typeof values) => (e: { target: { value: string } }) =>
    setValues((now) => ({ ...now, [name]: e.target.value }));
  const problem = (name: keyof typeof values) => problems[name] && t(problems[name]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (create.isPending) return;
    const sent = body();
    const checks = {
      username: sent.username === "" ? "validation.required" : usernameProblem(sent.username),
      firstName: nameProblem(sent.first_name),
      lastName: nameProblem(sent.last_name),
      email: emailProblem(sent.email),
    };
    const found = Object.fromEntries(
      Object.entries(checks).filter((entry): entry is [string, string] => entry[1] !== null),
    );
    setProblems(found);
    const refs = {
      username: usernameRef,
      firstName: firstNameRef,
      lastName: lastNameRef,
      email: emailRef,
    };
    const first = (Object.keys(refs) as (keyof typeof refs)[]).find((name) => found[name]);
    if (first) refs[first].current?.focus();
    else create.run();
  };

  return (
    <Dialog title={t("admin.newUser")} onClose={onClose}>
      <p className="muted">{t("admin.createIntro")}</p>
      <form onSubmit={submit} noValidate>
        <FormError error={create.error} />
        <Field
          label={t("admin.username")}
          hint={t("createAdmin.usernameHint")}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          value={values.username}
          onChange={set("username")}
          error={problem("username")}
          inputRef={usernameRef}
          required
        />
        <Field
          label={t("admin.firstName")}
          autoComplete="off"
          value={values.firstName}
          onChange={set("firstName")}
          error={problem("firstName")}
          inputRef={firstNameRef}
          required
        />
        <Field
          label={t("admin.lastName")}
          autoComplete="off"
          value={values.lastName}
          onChange={set("lastName")}
          error={problem("lastName")}
          inputRef={lastNameRef}
          required
        />
        <Field
          label={t("admin.email")}
          type="email"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          value={values.email}
          onChange={set("email")}
          error={problem("email")}
          inputRef={emailRef}
          required
        />
        <SelectField
          label={t("admin.role")}
          value={role}
          onChange={(e) => setRole(e.target.value as Role)}
        >
          <option value="INVESTOR">{t("roles.INVESTOR")}</option>
          <option value="ADMINISTRATOR">{t("roles.ADMINISTRATOR")}</option>
        </SelectField>
        <div className="actions">
          <button type="button" className="button" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button type="submit" className="button button-primary" disabled={create.isPending}>
            {t("admin.create")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** "Reimpostare…?" and "Disattivare…?": the action runs only from here (mockup 15's pattern
 * for what cannot be taken back: a red band, Annulla first). */
function ConfirmDialog<T>({
  heading,
  text,
  submit,
  path,
  onClose,
  onDone,
}: {
  heading: string;
  text: string;
  submit: string;
  path: string;
  onClose: () => void;
  onDone: (out: T) => void;
}) {
  const { t } = useTranslation();
  const action = useOneShot(() => api<T>("POST", path), onDone);
  return (
    <Dialog danger title={heading} onClose={onClose}>
      <p className="muted">{text}</p>
      <FormError error={action.error} />
      <div className="actions">
        <button type="button" className="button" onClick={onClose}>
          {t("common.cancel")}
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={action.isPending}
          onClick={action.run}
        >
          {submit}
        </button>
      </div>
    </Dialog>
  );
}

/** Mockup 15, "Password reimpostata": the temporary password, shown once, with Copia. */
function PasswordDialog({
  heading,
  text,
  password,
  onClose,
}: {
  heading: string;
  text: string;
  password: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const labelId = useId();
  const { notice, announce } = useNotice();
  const copy = () => {
    void navigator.clipboard?.writeText(password).then(() => announce(t("admin.copied")));
  };
  return (
    <Dialog title={heading} onClose={onClose}>
      <p className="muted">{text}</p>
      <p id={labelId} className="visually-hidden">
        {t("admin.temporaryLabel")}
      </p>
      <p className="temporary-password" aria-labelledby={labelId}>
        <code>{password}</code>
      </p>
      <Notice notice={notice} />
      <div className="actions">
        <button type="button" className="button" onClick={copy}>
          {t("admin.copy")}
        </button>
        <button type="button" className="button button-primary" onClick={onClose}>
          {t("admin.close")}
        </button>
      </div>
    </Dialog>
  );
}
