import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Navigate } from "react-router";

import { api } from "../api/client";
import type { Me } from "../api/types";
import { AuthLayout } from "../components/AuthLayout";
import { FormError } from "../components/FormError";
import { PageTitle } from "../components/PageTitle";
import { PasswordChangeForm } from "../components/PasswordChangeForm";
import { Loading } from "../components/Status";
import { logout, ME_KEY, useMe } from "../session";

/** A temporary password (new user, reset by the Administrator) must be changed first (D-78). */
export default function ForcedPasswordChange() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const me = useMe();
  const change = useMutation({
    mutationFn: ({ current, next }: { current: string; next: string }) =>
      api<Me>("POST", "/me/password", { current_password: current, new_password: next }),
    onSuccess: (updated) => client.setQueryData(ME_KEY, updated),
  });
  const leave = useMutation({ mutationFn: () => logout(client) });

  if (me.isPending) return <Loading />;
  if (!me.data) return <Navigate to="/login" replace />;
  if (!me.data.must_change_password) return <Navigate to="/" replace />;

  return (
    <AuthLayout>
      <PageTitle title={t("forcedChange.heading")} />
      <p className="muted">{t("forcedChange.intro")}</p>
      <PasswordChangeForm
        currentLabel={t("forcedChange.current")}
        onSubmit={(current, next) => change.mutate({ current, next })}
        pending={change.isPending}
        error={change.error}
      />
      <FormError error={leave.error} />
      <button type="button" className="button link-button" onClick={() => leave.mutate()}>
        {t("nav.logout")}
      </button>
    </AuthLayout>
  );
}
