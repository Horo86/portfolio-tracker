import { lazy, Suspense, useState } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useLocation } from "react-router";

import type { Me } from "../api/types";
import { BAR_PATHS, MORE_PATHS, NAV } from "./nav";
import { NavIcon, type NavIconName } from "./NavIcon";

// Loaded when "Altro" is first opened: the first screen stays small (D-138).
const MoreMenu = lazy(() => import("./MoreMenu"));

const ICON: Record<(typeof BAR_PATHS)[number], NavIconName> = {
  "/": "dashboard",
  "/holdings": "holdings",
  "/transactions": "transactions",
  "/import": "import",
};
const BAR = BAR_PATHS.map((path) => ({ ...NAV.find((item) => item.to === path)!, icon: ICON[path] }));

/** The compact shell's bar at the bottom (D-189): four pages and "Altro". */
export function BottomNav({ me, search }: { me: Me; search: string }) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  // "Altro" looks current on the pages it holds (a class only: a button is not a link).
  const inMore = MORE_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));

  return (
    <nav className="bottom-nav" aria-label={t("nav.label")}>
      <ul className="bottom-nav-list">
        {BAR.map((item) => (
          <li key={item.to}>
            <NavLink to={{ pathname: item.to, search }} end={item.end} className="bottom-nav-item">
              <NavIcon name={item.icon} />
              <span>{t(item.key)}</span>
            </NavLink>
          </li>
        ))}
        <li>
          <button
            type="button"
            className={inMore ? "bottom-nav-item is-current" : "bottom-nav-item"}
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => setOpen(true)}
          >
            <NavIcon name="more" />
            <span>{t("nav.more")}</span>
          </button>
        </li>
      </ul>
      {open && (
        <Suspense fallback={null}>
          <MoreMenu me={me} search={search} onClose={() => setOpen(false)} />
        </Suspense>
      )}
    </nav>
  );
}
