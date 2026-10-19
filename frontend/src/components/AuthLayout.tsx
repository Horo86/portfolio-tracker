import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** The card of the screens shown without a session (mockup 16). */
export function AuthLayout({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <main className="auth-page">
      <div className="card auth-card">
        <div className="brand">
          <img src="/icon.svg" alt="" width="40" height="40" />
          <span>{t("app.title")}</span>
        </div>
        {children}
      </div>
    </main>
  );
}
