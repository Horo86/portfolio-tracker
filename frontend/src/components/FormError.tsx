import { useTranslation } from "react-i18next";

import { errorText } from "../errors";

/** The translated error of a failed request, announced when it appears; `text` says it in
 * the screen's own words. */
export function FormError({ error, text }: { error: unknown; text?: string }) {
  const { t } = useTranslation();
  const message = error ? (text ?? errorText(t, error)) : null;
  return (
    <div role="alert" className={message ? "alert" : undefined}>
      {message}
    </div>
  );
}
