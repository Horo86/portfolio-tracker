import { useTranslation } from "react-i18next";

import { PageTitle } from "../components/PageTitle";

/** Placeholder until M5 slice 5 builds the dashboard (mockup 01). */
export default function Dashboard() {
  const { t } = useTranslation();
  return (
    <>
      <PageTitle title={t("dashboard.heading")} />
      <p className="muted">{t("dashboard.placeholder")}</p>
    </>
  );
}
