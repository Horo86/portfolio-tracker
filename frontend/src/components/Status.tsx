import { useTranslation } from "react-i18next";

import { errorText } from "../errors";

export function Loading() {
  const { t } = useTranslation();
  return (
    <p role="status" className="muted loading">
      {t("common.loading")}
    </p>
  );
}

export function LoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <div role="alert" className="alert">
      <p>{errorText(t, error)}</p>
      <button type="button" className="button" onClick={onRetry}>
        {t("common.retry")}
      </button>
    </div>
  );
}
