import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router";

import { api, ApiError } from "../api/client";
import type { Language, LoginOut } from "../api/types";
import { AuthLayout } from "../components/AuthLayout";
import { Field } from "../components/Field";
import { FormError } from "../components/FormError";
import { PageTitle } from "../components/PageTitle";
import { Loading } from "../components/Status";
import { LANGUAGES } from "../i18n";
import { setLanguage } from "../preferences";
import { useSecretMutation } from "../secretMutation";
import { SETUP_KEY, startSession, useSetupStatus } from "../session";
import { PASSWORD_MIN, passwordProblem, USERNAME_PATTERN } from "../validation";

type Stage = "welcome" | "admin" | "language";

/**
 * Mockup 16, first run: shown only while the instance has no user (D-34). "Start empty"
 * creates the Administrator in two steps; restore from a backup arrives in M7 (Q-21).
 */
export default function FirstRun() {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const navigate = useNavigate();
  const setup = useSetupStatus();
  const [stage, setStage] = useState<Stage>("welcome");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [problems, setProblems] = useState<Record<string, string>>({});
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const repeatRef = useRef<HTMLInputElement>(null);
  const create = useSecretMutation(
    (admin: { username: string; password: string; language: Language }) =>
      api<LoginOut>("POST", "/setup/admin", admin),
    {
      onSuccess: (out) => {
        startSession(client, out.user);
        client.setQueryData(SETUP_KEY, { setup_required: false });
        navigate("/", { replace: true });
      },
      onError: (error) => {
        // Another browser finished the setup meanwhile: the redirect below says so.
        if (error instanceof ApiError && error.code === "SETUP_ALREADY_DONE") {
          client.setQueryData(SETUP_KEY, { setup_required: false });
        }
      },
    },
  );

  if (setup.isPending) return <Loading />;
  if (setup.data && !setup.data.setup_required && !create.isSuccess) {
    const done = create.error instanceof ApiError && create.error.code === "SETUP_ALREADY_DONE";
    return <Navigate to="/login" replace state={done ? { notice: "SETUP_ALREADY_DONE" } : null} />;
  }

  const checkAdmin = (event: FormEvent) => {
    event.preventDefault();
    const found: Record<string, string> = {};
    if (!USERNAME_PATTERN.test(username)) found.username = "validation.usernamePattern";
    const problem = passwordProblem(password);
    if (problem) found.password = problem;
    else if (repeat !== password) found.repeat = "validation.passwordsDiffer";
    setProblems(found);
    const first = (["username", "password", "repeat"] as const).find((name) => found[name]);
    const refs = { username: usernameRef, password: passwordRef, repeat: repeatRef };
    if (first) refs[first].current?.focus();
    else setStage("language");
  };

  const steps = (
    <ol className="steps" aria-label={t("createAdmin.steps")}>
      {(["admin", "language"] as const).map((step, index) => (
        <li key={step} aria-current={stage === step ? "step" : undefined}>
          {index + 1}. {t(`createAdmin.step.${step}`)}
        </li>
      ))}
    </ol>
  );

  if (stage === "welcome") {
    return (
      <AuthLayout>
        <PageTitle title={t("firstRun.heading")} step={stage} />
        <p className="muted">{t("firstRun.intro")}</p>
        <div className="choices">
          <button
            type="button"
            className="button button-primary wide"
            onClick={() => setStage("admin")}
          >
            {t("firstRun.startEmpty")}
          </button>
          <button
            type="button"
            className="button wide"
            disabled
            aria-describedby="restore-unavailable"
          >
            {t("firstRun.restore")}
          </button>
          <p id="restore-unavailable" className="muted small">
            {t("firstRun.restoreUnavailable")}
          </p>
        </div>
      </AuthLayout>
    );
  }

  if (stage === "admin") {
    return (
      <AuthLayout>
        <PageTitle title={t("createAdmin.heading")} step={stage} />
        <p className="muted">{t("createAdmin.intro")}</p>
        {steps}
        <form onSubmit={checkAdmin} noValidate>
          <Field
            label={t("createAdmin.username")}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            hint={t("createAdmin.usernameHint")}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            error={problems.username && t(problems.username)}
            inputRef={usernameRef}
            required
          />
          <Field
            label={t("createAdmin.password")}
            type="password"
            autoComplete="new-password"
            hint={t("password.hint", { min: PASSWORD_MIN })}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={problems.password && t(problems.password, { min: PASSWORD_MIN })}
            inputRef={passwordRef}
            required
          />
          <Field
            label={t("createAdmin.repeat")}
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            error={problems.repeat && t(problems.repeat)}
            inputRef={repeatRef}
            required
          />
          <div className="actions">
            <button type="button" className="button" onClick={() => setStage("welcome")}>
              {t("common.back")}
            </button>
            <button type="submit" className="button button-primary">
              {t("common.continue")}
            </button>
          </div>
        </form>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <PageTitle title={t("createAdmin.languageHeading")} step={stage} />
      <p className="muted">{t("createAdmin.languageIntro")}</p>
      {steps}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          create.submit({ username, password, language: i18n.language as Language });
        }}
      >
        <FormError error={create.error} />
        <fieldset className="radio-group">
          <legend>{t("createAdmin.language")}</legend>
          {LANGUAGES.map((language) => (
            <label key={language} className="radio">
              <input
                type="radio"
                name="language"
                value={language}
                checked={i18n.language === language}
                onChange={() => setLanguage(language)}
              />
              {t(`languages.${language}`)}
            </label>
          ))}
        </fieldset>
        <div className="actions">
          <button type="button" className="button" onClick={() => setStage("admin")}>
            {t("common.back")}
          </button>
          <button type="submit" className="button button-primary" disabled={create.isPending}>
            {t("createAdmin.submit")}
          </button>
        </div>
      </form>
    </AuthLayout>
  );
}
