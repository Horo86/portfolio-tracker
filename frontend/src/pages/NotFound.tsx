import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { StateCard } from "../components/StateCard";

export default function NotFound() {
  const { t } = useTranslation();
  return (
    <StateCard
      icon="notFound"
      heading={t("notFound.heading")}
      level={1}
      actions={
        <Link to="/" className="button button-primary">
          {t("notFound.back")}
        </Link>
      }
    >
      <p className="muted">{t("notFound.text")}</p>
    </StateCard>
  );
}
