import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { PageTitle } from "../components/PageTitle";

export default function NotFound() {
  const { t } = useTranslation();
  return (
    <>
      <PageTitle title={t("notFound.heading")} />
      <p className="muted page-intro">{t("notFound.text")}</p>
      <Link to="/" className="button">
        {t("notFound.home")}
      </Link>
    </>
  );
}
