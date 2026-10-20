import { useId } from "react";
import { useTranslation } from "react-i18next";

import { useAccountInView } from "../accounts";

/** "Conto in vista" at the top of the sidebar (docs/ui/README.md): all accounts or one. */
export function AccountSelector({ reportingCurrency }: { reportingCurrency: string }) {
  const { t } = useTranslation();
  const { account, accounts, setAccount } = useAccountInView();
  const id = useId();
  return (
    <div className="account-view">
      <label htmlFor={id} className="account-view-label">
        {t("view.label")}
      </label>
      <select
        id={id}
        value={account?.id ?? ""}
        onChange={(e) => setAccount(e.target.value || null)}
        aria-describedby={`${id}-note`}
      >
        <option value="">{t("view.all")}</option>
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
          </option>
        ))}
      </select>
      <span id={`${id}-note`} className="nav-muted">
        {account
          ? t("view.oneNote", { currency: account.currency })
          : t("view.allNote", { count: accounts.length, currency: reportingCurrency })}
      </span>
    </div>
  );
}
