import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate } from "react-router";

import { AuthLayout } from "../components/AuthLayout";
import { FormError } from "../components/FormError";
import { PageTitle } from "../components/PageTitle";
import { Loading } from "../components/Status";
import { TwoFactorEnrol } from "../components/TwoFactorEnrol";
import { useScreenTexts } from "../i18n";
import { logout, ME_KEY, useMe } from "../session";
import { TWO_FACTOR_TEXTS } from "../twoFactorTexts";

/**
 * The enrolment an administrator cannot skip (D-174, `PT_REQUIRE_2FA=admins`): until it is
 * done the server answers 403 TWO_FACTOR_SETUP_REQUIRED everywhere else, so the guard brings
 * the administrator here, after a temporary password is changed. Log out is the only way out.
 */
export default function TwoFactorSetup() {
  useScreenTexts(TWO_FACTOR_TEXTS);
  const { t } = useTranslation();
  const client = useQueryClient();
  const me = useMe();
  const leave = useMutation({ mutationFn: () => logout(client) });
  // True while the recovery codes are on screen. The server already says the enrolment is
  // done, and `me` is read again by itself (when the tab is shown again, for one): the page
  // must not leave before "I saved the codes", they are never shown again.
  const [codesShown, setCodesShown] = useState(false);

  if (me.isPending) return <Loading />;
  if (!me.data) return <Navigate to="/login" replace />;
  if (me.data.must_change_password) return <Navigate to="/password-change" replace />;
  if (!me.data.two_factor_setup_required && !codesShown) return <Navigate to="/" replace />;

  return (
    <AuthLayout>
      <TwoFactorEnrol
        heading={(title, step) => <PageTitle title={title} step={step} />}
        intro={<p className="notice-warning">{t("twoFactor.setup.forcedIntro")}</p>}
        onCodesShown={() => setCodesShown(true)}
        onFinished={(user) => {
          client.setQueryData(ME_KEY, user);
          setCodesShown(false);
        }}
      />
      <FormError error={leave.error} />
      <button type="button" className="button link-button" onClick={() => leave.mutate()}>
        {t("nav.logout")}
      </button>
    </AuthLayout>
  );
}
