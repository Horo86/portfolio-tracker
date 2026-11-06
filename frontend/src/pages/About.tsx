import { useTranslation } from "react-i18next";

import { PageTitle } from "../components/PageTitle";
import { formatMomentDay } from "../format/date";
import { useHealth } from "../session";

const SOURCE = "https://github.com/Horo86/portfolio-tracker";

/** Mockup 18; the build date under the version (D-167). The table of third-party licences
 * arrives with 1.0.0; the image ships them in `licenses/THIRD-PARTY.txt`. */
export default function About() {
  const { t, i18n } = useTranslation();
  const health = useHealth();
  const buildDate = health.data?.build_date;
  return (
    <>
      <PageTitle title={t("about.heading")} />
      <p className="muted page-intro">{t("app.title")}</p>
      <section className="card">
        <dl className="rows">
          <div className="row">
            <dt>
              {t("about.version")}
              {health.data && (
                <span className="muted small">
                  {buildDate
                    ? t("about.builtOn", { date: formatMomentDay(buildDate, i18n.language) })
                    : t("about.developmentBuild")}
                </span>
              )}
            </dt>
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
