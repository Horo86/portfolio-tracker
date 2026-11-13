import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { type Location, Navigate, useLocation } from "react-router";

import { api, ApiError } from "../api/client";
import type { LoginOut, LoginStepOut, SecondFactor } from "../api/types";
import { AuthLayout } from "../components/AuthLayout";
import { Field } from "../components/Field";
import { FormError } from "../components/FormError";
import { PageTitle } from "../components/PageTitle";
import { Loading } from "../components/Status";
import { errorText } from "../errors";
import { useSecretMutation } from "../secretMutation";
import { startSession, useMe, useSetupStatus } from "../session";
import { compact, isAppCode } from "../twoFactor";

type LoginState = { from?: Location; notice?: string } | null;

const SCREENS_WITHOUT_SESSION = ["/login", "/welcome", "/password-change", "/two-factor-setup"];

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

/** The second step's answers that end it: back to the password, saying why (D-174). */
function endsTheStep(error: Error): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.code === "LOGIN_STEP_EXPIRED") return true;
  return error.code === "TWO_FACTOR_CODE_WRONG" && error.extra.attempts_left === 0;
}

/** Mockup 16, "Accedi", then "Verifica in due passaggi" for a user with two-factor
 * authentication (D-174): the right password opens no session yet, the code does. Every
 * address leads here without a session (no registration). */
export default function Login() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const state = useLocation().state as LoginState;
  const me = useMe();
  const setup = useSetupStatus();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [missing, setMissing] = useState({ username: false, password: false });
  // "code" once the password was right and a code is needed; the proof is in a cookie.
  const [stage, setStage] = useState<"password" | "code">("password");
  const [stepOver, setStepOver] = useState<ApiError | null>(null);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const login = useSecretMutation(
    (credentials: { username: string; password: string }) =>
      api<LoginOut | LoginStepOut>("POST", "/auth/login", credentials),
    {
      onSuccess: (out) => {
        setPassword("");
        if ("two_factor_required" in out) setStage("code");
        else startSession(client, out.user);
      },
      onError: () => setPassword(""),
    },
  );

  if (me.data) return <Navigate to={returnPath(state?.from)} replace />;
  if (setup.data?.setup_required) return <Navigate to="/welcome" replace />;
  if (me.isPending) return <Loading />;

  if (stage === "code") {
    return (
      <SecondStep
        onBack={(why) => {
          setStepOver(why);
          setStage("password");
        }}
      />
    );
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found = { username: !username, password: !password };
    setMissing(found);
    if (found.username) usernameRef.current?.focus();
    else if (found.password) passwordRef.current?.focus();
    else {
      setStepOver(null);
      login.submit({ username, password });
    }
  };
  // A notice from another screen (e.g. the setup was already done meanwhile).
  const notice = state?.notice ? new ApiError(409, state.notice) : null;
  const shown = login.error ?? stepOver ?? notice;
  const tooManyCodes = shown === stepOver && stepOver?.code === "TWO_FACTOR_CODE_WRONG";

  return (
    <AuthLayout>
      <PageTitle title={t("login.heading")} step={stage} />
      <p className="muted">{t("login.intro")}</p>
      <form onSubmit={submit} noValidate>
        <FormError error={shown} text={tooManyCodes ? t("login.tooManyCodes") : undefined} />        <Field
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

/** "Verifica in due passaggi" (mockup 16): a code of the authenticator app, or a recovery
 * code. A wrong code keeps the step, with the attempts left; too many, or the 5 minutes over,
 * and the login starts again from the password. Its 401s are not a session ending (client). */
function SecondStep({ onBack }: { onBack: (why: ApiError | null) => void }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [recovery, setRecovery] = useState(false);
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const fieldRef = useRef<HTMLInputElement>(null);
  const step = useSecretMutation(
    (factor: SecondFactor) => api<LoginOut>("POST", "/auth/login/two-factor", factor),
    {
      onSuccess: (out) => startSession(client, out.user),
      onError: (error) => {
        setTyped("");
        if (endsTheStep(error)) onBack(error as ApiError);
      },
    },
  );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (step.isPending) return;
    const value = typed.trim();
    const wrong = !value ? "validation.required" : !recovery && !isAppCode(value) ? "login.codeFormat" : null;
    setProblem(wrong && t(wrong));
    if (wrong) fieldRef.current?.focus();
    else step.submit(recovery ? { recovery_code: value } : { code: compact(value) });
  };
  const switchKind = () => {
    setRecovery((now) => !now);
    setTyped("");
    setProblem(null);
    step.reset();
    fieldRef.current?.focus();
  };

  const error = step.error;
  const left = error instanceof ApiError ? error.extra.attempts_left : undefined;
  const text =
    error instanceof ApiError && error.code === "TWO_FACTOR_CODE_WRONG" && typeof left === "number"
      ? `${errorText(t, error)} ${t("login.attemptsLeft", { count: left })}`
      : undefined;

  return (
    <AuthLayout>
      <PageTitle title={t("login.codeHeading")} step="code" />
      <p className="muted">{t(recovery ? "login.recoveryIntro" : "login.codeIntro")}</p>
      <form onSubmit={submit} noValidate>
        <FormError error={error} text={text} />
        <Field
          label={t(recovery ? "login.recovery" : "login.code")}
          name={recovery ? "recovery-code" : "one-time-code"}
          autoComplete={recovery ? "off" : "one-time-code"}
          inputMode={recovery ? "text" : "numeric"}
          autoCapitalize="none"
          spellCheck={false}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          error={problem ?? undefined}
          inputRef={fieldRef}
          required
        />
        <button type="submit" className="button button-primary wide" disabled={step.isPending}>
          {t("login.confirm")}
        </button>
      </form>
      <p className="small">
        <button type="button" className="link-button" onClick={switchKind}>
          {t(recovery ? "login.useApp" : "login.useRecovery")}
        </button>
      </p>
      <p className="small">
        <button type="button" className="link-button" onClick={() => onBack(null)}>
          {t("login.backToPassword")}
        </button>
      </p>
    </AuthLayout>
  );
}
