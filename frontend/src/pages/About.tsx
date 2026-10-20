import { useTranslation } from "react-i18next";

import { PageTitle } from "../components/PageTitle";
import { useHealth } from "../session";

const SOURCE = "https://github.com/Horo86/portfolio-tracker";

/** Mockup 18. The list of third-party licences arrives with the release (M7). */
export default function About() {
  const { t } = useTranslation();
  const health = useHealth();
  return (
    <>
      <PageTitle title={t("about.heading")} />
      <p className="muted page-intro">{t("app.title")}</p>
      <section className="card">
        <dl className="rows">
          <div className="row">
            <dt>{t("about.version")}</dt>
            <dd className="num">{health.data?.version ?? "…"}</dd>
          </div>
          <div className="row">
            <dt>
              {t("about.license")}
              <span className="muted small">{t("about.licenseText")}</span>
            </dt>
            <dd>
              <a href={`${SOURCE}/blob/main/LICENSE`} rel="noreferrer">
                {t("about.readLicense")}
              </a>
            </dd>
          </div>
          <div className="row">
            <dt>
              {t("about.source")}
              <span className="muted small">{t("about.sourceHint")}</span>
            </dt>
            <dd>
              <a href={SOURCE} rel="noreferrer">
                github.com/Horo86/portfolio-tracker
              </a>
            </dd>
          </div>
        </dl>
      </section>
    </>
  );
}
