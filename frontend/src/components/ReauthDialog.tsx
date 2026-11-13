import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { useOneShot } from "../secretMutation";
import { secondFactor } from "../twoFactor";
import { Dialog } from "./Dialog";
import { Field } from "./Field";
import { FormError } from "./FormError";

type Props<T> = {
  heading: string;
  text: string;
  submit: string;
  path: string; // POST, with { password } and, with `withCode`, a code or a recovery code
  withCode: boolean;
  passwordLabel: string;
  passwordHint?: string;
  codeLabel: string;
  codeHint: string;
  danger?: boolean;
  onClose: () => void;
  onDone: (out: T) => void;
};

/**
 * An action that asks again who is acting (mockup 15's pattern for what cannot be taken back):
 * the password and, with two-factor authentication, a code of the authenticator app or a
 * recovery code (D-174). The Users screen's reset and disable-2FA, Settings' turning off and
 * new recovery codes. What is typed stays in this dialog's state and is read when the request
 * is sent: it is never a mutation's variables (`useOneShot`), so never in a cache; the answer,
 * which may carry something shown once, goes straight to `onDone`.
 */
export function ReauthDialog<T>({
  heading,
  text,
  submit: submitLabel,
  path,
  withCode,
  passwordLabel,
  passwordHint,
  codeLabel,
  codeHint,
  danger = true,
  onClose,
  onDone,
}: Props<T>) {
  const { t } = useTranslation();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [problems, setProblems] = useState<{ password?: string; code?: string }>({});
  const passwordRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const action = useOneShot(
    () => api<T>("POST", path, { password, ...(withCode ? secondFactor(code) : {}) }),
    onDone,
    () => setCode(""), // a code works once: a new one is needed anyway
  );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (action.isPending) return;
    const found = {
      password: password ? undefined : "validation.required",
      code: withCode && !code.trim() ? "validation.required" : undefined,
    };
    setProblems(found);
    if (found.password) passwordRef.current?.focus();
    else if (found.code) codeRef.current?.focus();
    else action.run();
  };

  return (
    <Dialog danger={danger} title={heading} onClose={onClose}>
      <p className="muted">{text}</p>
      <form onSubmit={submit} noValidate>
        <FormError error={action.error} />
        <Field
          label={passwordLabel}
          type="password"
          autoComplete="current-password"
          hint={passwordHint}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={problems.password && t(problems.password)}
          inputRef={passwordRef}
          required
        />
        {withCode && (
          <Field
            label={codeLabel}
            autoComplete="one-time-code"
            autoCapitalize="none"
            spellCheck={false}
            hint={codeHint}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            error={problems.code && t(problems.code)}
            inputRef={codeRef}
            required
          />
        )}
        <div className="actions">
          <button type="button" className="button" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="submit"
            className={danger ? "button button-danger" : "button button-primary"}
            disabled={action.isPending}
          >
            {submitLabel}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
