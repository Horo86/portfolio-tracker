import { useTranslation } from "react-i18next";

import type { Me } from "../api/types";
import { AccountSelector } from "./AccountSelector";

/** The compact shell's top strip (D-189): the icon and "Conto in vista". Not sticky. */
export function TopStrip({ me }: { me: Me }) {
  const { t } = useTranslation();
  return (
    <header className="top-strip">
      <div className="brand">
        <img src="/icon.svg" alt="" width="32" height="32" />
        <span className="visually-hidden">{t("app.title")}</span>
      </div>
      <AccountSelector reportingCurrency={me.reporting_currency} />
    </header>
  );
}
