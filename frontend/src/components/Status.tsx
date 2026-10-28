import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import type { Me } from "../api/types";
import { ME_KEY } from "../session";
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
 * again, the code and "Riprova". Only the Administrator runs the container, so only they are
 * sent to its logs; anyone else, or a user not known yet, is asked to tell the Administrator. */
export function LoadError({ error, onRetry, level }: LoadErrorProps) {
  const { t } = useTranslation();
  // Read from the cache, never asked for: this card must not start a request of its own.
  const role = useQueryClient().getQueryData<Me | null>(ME_KEY)?.role;
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
        {own ? errorText(t, error) : t("loadError.text")} {t(role === "ADMINISTRATOR" ? "loadError.hintLogs" : "loadError.hintAdmin")}
        {error instanceof ApiError && <> {t("loadError.code", { code: error.code })}</>}
      </p>
    </StateCard>
  );
}
