import { type FormEvent, type ReactNode, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { PASSWORD_MIN, passwordProblem } from "../validation";
import { Field } from "./Field";
import { FormError } from "./FormError";

type Props = {
  onSubmit: (current: string, next: string) => void;
  pending: boolean;
  error: unknown;
  currentLabel?: string;
  actions?: ReactNode; // e.g. a Cancel button in a dialog
};

/** Current, new and repeated password (mockup 15, "Cambia password"). */
export function PasswordChangeForm({ onSubmit, pending, error, currentLabel, actions }: Props) {
  const { t } = useTranslation();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [problems, setProblems] = useState<Record<string, string>>({});
  const currentRef = useRef<HTMLInputElement>(null);
  const nextRef = useRef<HTMLInputElement>(null);
  const repeatRef = useRef<HTMLInputElement>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found: Record<string, string> = {};
    if (!current) found.current = "validation.required";
    const problem = passwordProblem(next);
    if (problem) found.next = problem;
    else if (repeat !== next) found.repeat = "validation.passwordsDiffer";
    setProblems(found);
    const first = (["current", "next", "repeat"] as const).find((name) => found[name]);
    const refs = { current: currentRef, next: nextRef, repeat: repeatRef };
    if (first) refs[first].current?.focus();
    else onSubmit(current, next);
  };

  return (
    <form onSubmit={submit} noValidate>
      <FormError error={error} />
      <Field
        label={currentLabel ?? t("password.current")}
        type="password"
        autoComplete="current-password"
        value={current}
        onChange={(e) => setCurrent(e.target.value)}
        error={problems.current && t(problems.current)}
        inputRef={currentRef}
        required
      />
      <Field
        label={t("password.new")}
        type="password"
        autoComplete="new-password"
        hint={t("password.hint", { min: PASSWORD_MIN })}
        value={next}
        onChange={(e) => setNext(e.target.value)}
        error={problems.next && t(problems.next, { min: PASSWORD_MIN })}
        inputRef={nextRef}
        required
      />
      <Field
        label={t("password.repeat")}
        type="password"
        autoComplete="new-password"
        value={repeat}
        onChange={(e) => setRepeat(e.target.value)}
        error={problems.repeat && t(problems.repeat)}
        inputRef={repeatRef}
        required
      />
      <div className="actions">
        {actions}
        <button type="submit" className="button button-primary" disabled={pending}>
          {t("password.submit")}
        </button>
      </div>
    </form>
  );
}
