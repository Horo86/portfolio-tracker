import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { errorText, hasOwnText } from "../errors";
import { StateCard } from "./StateCard";

export function Loading() {
  const { t } = useTranslation();
  return (
    <p role="status" className="muted loading">
      {t("common.loading")}
    </p>
  );
}

type LoadErrorProps = {
  error: unknown;
  onRetry: () => void;
  // 1 where the page has no title of its own, 3 inside a card or a dialog.
  level?: 1 | 2 | 3;
};

/** A load that failed (mockup 19, generic error): what happened, where to look if it happens
 * again, the code and "Riprova". */
export function LoadError({ error, onRetry, level }: LoadErrorProps) {
  const { t } = useTranslation();
  const own = hasOwnText(t, error);
  return (
    <StateCard
      icon="error"
      role="alert"
      heading={t("loadError.heading")}
      level={level}
      actions={
        <button type="button" className="button button-primary" onClick={onRetry}>
          {t("common.retry")}
        </button>
      }
    >
      <p className="muted">
        {own ? errorText(t, error) : t("loadError.text")} {t("loadError.hint")}
        {error instanceof ApiError && <> {t("loadError.code", { code: error.code })}</>}
      </p>
    </StateCard>
  );
}
