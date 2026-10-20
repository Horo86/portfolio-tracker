import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Suspense } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, Outlet } from "react-router";

import { useAccountInView } from "../accounts";
import type { Me } from "../api/types";
import { logout, useHealth } from "../session";
import { AccountSelector } from "./AccountSelector";
import { FormError } from "./FormError";
import { Loading } from "./Status";

// Entries appear as their pages are built (Titoli, Transazioni, Importa: later slices;
// Amministrazione: M6, Q-1).
const NAV = [
  { to: "/", key: "nav.dashboard", end: true },
  { to: "/accounts", key: "nav.accounts", end: false },
  { to: "/settings", key: "nav.settings", end: false },
] as const;

/** The shell of every page with a session (mockups 13, 14): sidebar and content. */
export function AppLayout({ me }: { me: Me }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const health = useHealth();
  // Every link keeps the account in view (Q-6).
  const { search } = useAccountInView();
  const leave = useMutation({ mutationFn: () => logout(client) });
  const name = me.display_name ?? me.username;

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        {t("app.skipToContent")}
      </a>
      <aside className="sidebar">
        <div className="brand">
          <img src="/icon.svg" alt="" width="36" height="36" />
          <span>{t("app.title")}</span>
        </div>
        <AccountSelector reportingCurrency={me.reporting_currency} />
        <nav aria-label={t("nav.label")}>
          <ul className="nav-list">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink to={{ pathname: item.to, search }} end={item.end} className="nav-link">
                  {t(item.key)}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="sidebar-bottom">
          <div className="user-box">
            <span className="avatar" aria-hidden="true">
              {name.charAt(0).toUpperCase()}
            </span>
            <span className="user-text">
              <span className="user-name">{name}</span>
              <span className="nav-muted">{t(`roles.${me.role}`)}</span>
            </span>
          </div>
          <FormError error={leave.error} />
          <button
            type="button"
            className="button nav-button"
            onClick={() => leave.mutate()}
            disabled={leave.isPending}
          >
            {t("nav.logout")}
          </button>
          <Link to={{ pathname: "/about", search }} className="nav-footer">
            {t("nav.about")}
            {health.data && ` · ${t("nav.version", { version: health.data.version })}`}
          </Link>
        </div>
      </aside>
      <main id="main" className="content" tabIndex={-1}>
        <Suspense fallback={<Loading />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
