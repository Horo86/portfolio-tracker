import { type FormEvent, type ReactNode, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import type { Me, RecoveryCodesOut, TwoFactorSetup } from "../api/types";
import { useOneShot } from "../secretMutation";
import { compact, groupSecret, isAppCode, qrPath } from "../twoFactor";
import { Field } from "./Field";
import { FormError } from "./FormError";
import { Notice, useNotice } from "./Notice";

// A QR code needs a light margin of four modules around it to be read (ISO/IEC 18004).
const QUIET_ZONE = 4;

/** The QR code the server sent as rows of "0" and "1", drawn as an inline SVG: dark on light
 * in every theme (an app reads it so), no HTML injected, nothing for the CSP (D-163). */
export function QrCode({ rows, label }: { rows: readonly string[]; label: string }) {
  const size = (rows[0]?.length ?? 0) + 2 * QUIET_ZONE;
  return (
    <svg
      className="qr-code"
      role="img"
      aria-label={label}
      viewBox={`0 0 ${size} ${size}`}
      width="200"
      height="200"
      shapeRendering="crispEdges"
    >
      <rect width={size} height={size} fill="#ffffff" />
      <path d={qrPath(rows, QUIET_ZONE)} fill="#000000" />
    </svg>
  );
}

/** Mockup 15, "Codici di recupero": shown once; the user says they are saved to go on. */
export function RecoveryCodes({
  codes,
  onSaved,
}: {
  codes: readonly string[];
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const listId = useId();
  const { notice, announce } = useNotice();
  const copy = () => {
    void navigator.clipboard
      ?.writeText(codes.join("\n"))
      .then(() => announce(t("twoFactor.codes.copied")));
  };
  return (
    <>
      <p className="muted">{t("twoFactor.codes.intro")}</p>
      <p id={listId} className="visually-hidden">
        {t("twoFactor.codes.heading")}
      </p>
      <ul className="recovery-codes" aria-labelledby={listId}>
        {codes.map((code) => (
          <li key={code}>
            <code>{code}</code>
          </li>
        ))}
      </ul>
      <p className="notice-warning">{t("twoFactor.codes.keep")}</p>
      <Notice notice={notice} />
      <div className="actions">
        <button type="button" className="button" onClick={copy}>
          {t("twoFactor.codes.copy")}
        </button>
        <button type="button" className="button button-primary" onClick={onSaved}>
          {t("twoFactor.codes.saved")}
        </button>
      </div>
    </>
  );
}

export type EnrolStep = "password" | "scan" | "codes";

type Props = {
  // The heading of each step: a page's <h1> or a dialog's title.
  heading: (title: string, step: EnrolStep) => ReactNode;
  // Said before the password: why (the obligation) or nothing.
  intro?: ReactNode;
  // After "I saved the codes": the updated user, to put in the `me` query.
  onFinished: (user: Me) => void;
  // A Cancel button next to the submit buttons (a dialog), none on the forced page.
  onCancel?: () => void;
};

// The server's clock against this device's: further than one step apart, codes may fail.
const SKEW_WARNING_SECONDS = 30;

/**
 * Turning on two-factor authentication (D-174): the password, then the QR code with the key
 * as text and a first code to confirm, then the ten recovery codes, shown once. The secret
 * and the codes live only in this component's state while it shows them (`useOneShot`),
 * never in the query or mutation cache; what is typed is never a mutation's variables.
 */
export function TwoFactorEnrol({ heading, intro, onFinished, onCancel }: Props) {
  const { t } = useTranslation();
  const [step, setStep] = useState<EnrolStep>("password");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [setup, setSetup] = useState<(TwoFactorSetup & { skew: number }) | null>(null);
  const [done, setDone] = useState<{ user: Me; codes: string[] } | null>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  const start = useOneShot(
    () => api<TwoFactorSetup>("POST", "/me/two-factor/setup", { password }),
    (out) => {
      setPassword("");
      const skew = Math.round((Date.parse(out.server_time) - Date.now()) / 1000);
      setSetup({ ...out, skew });
      setStep("scan");
    },
  );
  const confirm = useOneShot(
    () => api<RecoveryCodesOut>("POST", "/me/two-factor/confirm", { code: compact(code) }),
    (out) => {
      setSetup(null); // the secret leaves the page once it is confirmed
      setCode("");
      setDone({ user: out.user, codes: out.recovery_codes });
      setStep("codes");
    },
    (error) => {
      // Shown too long ago (15 minutes): the setup starts again from the password, saying why.
      if (error instanceof ApiError && error.code === "TWO_FACTOR_NOT_STARTED") {
        setSetup(null);
        setCode("");
        setStep("password");
      }
    },
  );
  const expired =
    confirm.error instanceof ApiError && confirm.error.code === "TWO_FACTOR_NOT_STARTED"
      ? confirm.error
      : null;

  const submitPassword = (event: FormEvent) => {
    event.preventDefault();
    if (start.isPending) return;
    if (!password) {
      setProblem(t("validation.required"));
      passwordRef.current?.focus();
      return;
    }
    setProblem(null);
    confirm.reset();
    start.run();
  };
  const submitCode = (event: FormEvent) => {
    event.preventDefault();
    if (confirm.isPending) return;
    if (!isAppCode(code)) {
      setProblem(t(code.trim() ? "login.codeFormat" : "validation.required"));
      codeRef.current?.focus();
      return;
    }
    setProblem(null);
    confirm.run();
  };
  const cancel = onCancel && (
    <button type="button" className="button" onClick={onCancel}>
      {t("common.cancel")}
    </button>
  );

  if (step === "codes" && done) {
    return (
      <>
        {heading(t("twoFactor.codes.heading"), step)}
        <RecoveryCodes
          codes={done.codes}
          onSaved={() => {
            const { user } = done;
            setDone(null); // the codes leave the page's state
            onFinished(user);
          }}
        />
      </>
    );
  }

  if (step === "scan" && setup) {
    return (
      <>
        {heading(t("twoFactor.setup.heading"), step)}
        <p className="muted">{t("twoFactor.setup.scan")}</p>
        <div className="qr-frame">
          <QrCode rows={setup.qr} label={t("twoFactor.setup.qr")} />
        </div>
        <p className="muted small">{t("twoFactor.setup.manual")}</p>
        <p className="temporary-password two-factor-key">
          <span className="visually-hidden">{t("twoFactor.setup.key")}: </span>
          <code>{groupSecret(setup.secret)}</code>
        </p>
        {Math.abs(setup.skew) >= SKEW_WARNING_SECONDS && (
          <p className="notice-warning">
            {t("twoFactor.setup.clock", { count: Math.abs(setup.skew) })}
          </p>
        )}
        <form onSubmit={submitCode} noValidate>
          <FormError error={confirm.error} />
          <Field
            label={t("twoFactor.setup.code")}
            name="one-time-code"
            autoComplete="one-time-code"
            inputMode="numeric"
            autoCapitalize="none"
            spellCheck={false}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            error={problem ?? undefined}
            inputRef={codeRef}
            required
          />
          <div className="actions">
            {cancel}
            <button type="submit" className="button button-primary" disabled={confirm.isPending}>
              {t("twoFactor.setup.confirm")}
            </button>
          </div>
        </form>
      </>
    );
  }

  return (
    <>
      {heading(t("twoFactor.setup.heading"), "password")}
      {intro}
      <p className="muted">{t("twoFactor.setup.passwordIntro")}</p>
      <form onSubmit={submitPassword} noValidate>
        <FormError error={start.error ?? expired} />
        <Field
          label={t("twoFactor.setup.password")}
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={problem ?? undefined}
          inputRef={passwordRef}
          required
        />
        <div className="actions">
          {cancel}
          <button type="submit" className="button button-primary" disabled={start.isPending}>
            {t("common.continue")}
          </button>
        </div>
      </form>
    </>
  );
}
