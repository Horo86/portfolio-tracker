import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router";

import type { Me } from "../api/types";
import { logout, useHealth } from "../session";
import { Dialog } from "./Dialog";
import { FormError } from "./FormError";
import { ADMIN_ITEM, NAV } from "./nav";

type Props = { me: Me; search: string; onClose: () => void };

/** "Altro" in the compact shell (D-189): the pages not in the bar, the user and log out. */
export default function MoreMenu({ me, search, onClose }: Props) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const health = useHealth();
  const leave = useMutation({ mutationFn: () => logout(client) });
  const name = `${me.first_name} ${me.last_name}`;
  const pages = [
    NAV.find((item) => item.to === "/accounts")!,
    NAV.find((item) => item.to === "/settings")!,
    ...(me.role === "ADMINISTRATOR" ? [ADMIN_ITEM] : []),
  ];

  return (
    <Dialog title={t("nav.more")} onClose={onClose} menu>
      <div className="user-box">
        <span className="avatar" aria-hidden="true">
          {name.charAt(0).toUpperCase()}
        </span>
        <span className="user-text">
          <span className="user-name">{name}</span>
          <span className="muted small">{t(`roles.${me.role}`)}</span>
        </span>
      </div>
      <ul className="more-list" aria-label={t("nav.moreLabel")}>
        {pages.map((item) => (
          <li key={item.to}>
            <NavLink to={{ pathname: item.to, search }} className="more-link" onClick={onClose}>
              {t(item.key)}
            </NavLink>
          </li>
        ))}
        <li>
          <NavLink to={{ pathname: "/about", search }} className="more-link" onClick={onClose}>
            {t("nav.about")}
            {health.data && ` · ${t("nav.version", { version: health.data.version })}`}
            {health.data?.preview && ` · ${t("nav.preview")}`}
          </NavLink>
        </li>
      </ul>
      <FormError error={leave.error} />
      <div className="more-actions">
        <button
          type="button"
          className="button"
          onClick={() => leave.mutate()}
          disabled={leave.isPending}
        >
          {t("nav.logout")}
        </button>
        <button type="button" className="button" onClick={onClose}>
          {t("nav.close")}
        </button>
      </div>
    </Dialog>
  );
}
