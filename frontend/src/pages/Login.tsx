import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { startSession, useMe, useSetupStatus } from "../session";

type LoginState = { from?: Location; notice?: string } | null;

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
  const login = useMutation({
    mutationFn: () => api<LoginOut>("POST", "/auth/login", { username, password }),
    onSuccess: (out) => startSession(client, out.user),
    onError: () => setPassword(""),
  });

  if (me.data) {
    const from = state?.from;
    return <Navigate to={from ? from.pathname + from.search : "/"} replace />;
  }
  if (setup.data?.setup_required) return <Navigate to="/welcome" replace />;
  if (me.isPending) return <Loading />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found = { username: !username, password: !password };
    setMissing(found);
    if (found.username) usernameRef.current?.focus();
    else if (found.password) passwordRef.current?.focus();
    else login.mutate();
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
