import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { Me } from "../api/types";
import { formatMomentDay } from "../format/date";
import { useScreenTexts } from "../i18n";
import { ME_KEY } from "../session";
import { TWO_FACTOR_TEXTS } from "../twoFactorTexts";
import { FormError } from "./FormError";
import { focusPageTitle } from "./PageTitle";

/**
 * D-174 (8): an administrator or the container command turned the user's two-factor
 * authentication off. Said on every page, with the date and never by whom, until it is on
 * again or the user dismisses it ("Ho capito").
 */
export default function TwoFactorNotice({ disabledAt }: { disabledAt: string }) {
  useScreenTexts(TWO_FACTOR_TEXTS);
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const dismiss = useMutation({
    mutationFn: () => api<Me>("POST", "/me/two-factor/notice/dismiss"),
    onSuccess: (me) => {
      client.setQueryData(ME_KEY, me);
      focusPageTitle(); // the button that had the focus goes away with the notice
    },
  });
  return (
    <div className="notice-warning two-factor-notice">
      <p>{t("twoFactor.notice.text", { date: formatMomentDay(disabledAt, i18n.language) })}</p>
      <FormError error={dismiss.error} />
      <button
        type="button"
        className="button"
        disabled={dismiss.isPending}
        onClick={() => dismiss.mutate()}
      >
        {t("twoFactor.notice.dismiss")}
      </button>
    </div>
  );
}
