import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { type Location, Navigate, useLocation } from "react-router";

import { api, ApiError } from "../api/client";
import type { LoginOut } from "../api/types";
import { AuthLayout } from "../components/AuthLayout";
import { Field } from "../components/Field";
import { FormError } from "../components/FormError";
import { PageTitle } from "../components/PageTitle";
import { Loading } from "../components/Status";
import { useSecretMutation } from "../secretMutation";
import { startSession, useMe, useSetupStatus } from "../session";

type LoginState = { from?: Location; notice?: string } | null;

const SCREENS_WITHOUT_SESSION = ["/login", "/welcome", "/password-change"];

/**
 * Where to go after login: the page that asked for it, if it is a path of this app. Anything
 * else (`//host`, a backslash, a screen without session) goes to the dashboard, so a crafted
 * link cannot send the user elsewhere or break the page.
 */
export function returnPath(from: Pick<Location, "pathname" | "search"> | undefined): string {
  const path = from?.pathname ?? "";
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) return "/";
  if (SCREENS_WITHOUT_SESSION.includes(path)) return "/";
  return path + (from?.search ?? "");
}

/** Mockup 16, "Accedi". Every address leads here without a session (no registration). */
export default function Login() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const state = useLocation().state as LoginState;
  const me = useMe();
  const setup = useSetupStatus();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [missing, setMissing] = useState({ username: false, password: false });
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const login = useSecretMutation(
    (credentials: { username: string; password: string }) =>
      api<LoginOut>("POST", "/auth/login", credentials),
    {
      onSuccess: (out) => startSession(client, out.user),
      onError: () => setPassword(""),
    },
  );

  if (me.data) return <Navigate to={returnPath(state?.from)} replace />;
  if (setup.data?.setup_required) return <Navigate to="/welcome" replace />;
  if (me.isPending) return <Loading />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found = { username: !username, password: !password };
    setMissing(found);
    if (found.username) usernameRef.current?.focus();
    else if (found.password) passwordRef.current?.focus();
    else login.submit({ username, password });
  };
  // A notice from another screen (e.g. the setup was already done meanwhile).
  const notice = state?.notice ? new ApiError(409, state.notice) : null;

  return (
    <AuthLayout>
      <PageTitle title={t("login.heading")} />
      <p className="muted">{t("login.intro")}</p>
      <form onSubmit={submit} noValidate>
        <FormError error={login.error ?? notice} />
        <Field
          label={t("login.username")}
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          error={missing.username ? t("validation.required") : undefined}
          inputRef={usernameRef}
          required
        />
        <Field
          label={t("login.password")}
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={missing.password ? t("validation.required") : undefined}
          inputRef={passwordRef}
          required
        />
        <button type="submit" className="button button-primary wide" disabled={login.isPending}>
          {t("login.submit")}
        </button>
      </form>
      <p className="muted small">{t("login.forgot")}</p>
    </AuthLayout>
  );
}
