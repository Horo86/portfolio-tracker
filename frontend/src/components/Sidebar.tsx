import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, NavLink } from "react-router";

import type { Me } from "../api/types";
import { logout, useHealth } from "../session";
import { AccountSelector } from "./AccountSelector";
import { FormError } from "./FormError";
import { ADMIN_NAV, NAV } from "./nav";

/** The desktop shell's sidebar (mockups 13, 14), 1024 px and wider. */
export function Sidebar({ me, search }: { me: Me; search: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const health = useHealth();
  const leave = useMutation({ mutationFn: () => logout(client) });
  const name = `${me.first_name} ${me.last_name}`;

  return (
    <aside className="sidebar">
      <div className="brand">
        <img src="/icon.svg" alt="" width="36" height="36" />
        <span>{t("app.title")}</span>
      </div>
      <AccountSelector reportingCurrency={me.reporting_currency} />
      <nav aria-label={t("nav.label")}>
        <ul className="nav-list">
          {(me.role === "ADMINISTRATOR" ? ADMIN_NAV : NAV).map((item) => (
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
          {health.data?.preview && ` · ${t("nav.preview")}`}
        </Link>
      </div>
    </aside>
  );
}
