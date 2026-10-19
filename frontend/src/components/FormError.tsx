import { useTranslation } from "react-i18next";

import { errorText } from "../errors";

/** The translated error of a failed request, announced when it appears. */
export function FormError({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <div role="alert" className={error ? "alert" : undefined}>
      {error ? errorText(t, error) : null}
    </div>
  );
}
