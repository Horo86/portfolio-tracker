import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { LicenceRow, SystemInfo } from "../api/types";
import { PageTitle } from "../components/PageTitle";
import { formatMoment, formatMomentDay } from "../format/date";
import { formatSize } from "../format/size";
import { useHealth, useMe } from "../session";

const SOURCE = "https://github.com/Horo86/portfolio-tracker";

/** Mockup 18; the build date under the version (D-167); for administrators, the System
 * section (D-168 (6)); for everyone, the third-party licences of the web app and the server,
 * from the lists the image build writes in `licenses/` (D-167). */
export default function About() {
  const { t, i18n } = useTranslation();
  const health = useHealth();
  const admin = useMe().data?.role === "ADMINISTRATOR";
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
      <LicencesSection />
      {admin && <SystemSection />}
    </>
  );
}

/** What an administrator needs after an upgrade: the schema, the last upgrade and where its
 * copy is, the kept copies and the latest backup. Requested by administrators only. */
function SystemSection() {
  const { t, i18n } = useTranslation();
  const info = useQuery({
    queryKey: ["admin", "system"],
    queryFn: ({ signal }) => api<SystemInfo>("GET", "/admin/system", undefined, signal),
  });
  const data = info.data;
  const when = (moment: string | null) => (moment ? formatMoment(moment, i18n.language) : "?");
  const last = data?.last_upgrade;
  const backup = data?.latest_backup;
  return (
    <section className="card" aria-labelledby="about-system">
      <h2 id="about-system">{t("about.system")}</h2>
      <p className="muted small">{t("about.systemHint")}</p>
      {data && (
        <dl className="rows">
          <div className="row">
            <dt>{t("about.schema")}</dt>
            <dd className="num">
              {data.schema_revision}
              {data.schema_revision === data.schema_head && (
                <span className="muted small"> · {t("about.schemaCurrent")}</span>
              )}
            </dd>
          </div>
          <div className="row">
            <dt>
              {t("about.lastUpgrade")}
              {last && (
                <span className="muted small">
                  {t("about.upgradeCopy", { path: last.copy_path })}
                </span>
              )}
            </dt>
            <dd>
              {last
                ? t(last.status === "failed" ? "about.upgradeFailed" : "about.upgradeDone", {
                    from: last.from_revision ?? "?",
                    to: last.to_revision ?? "?",
                    version: last.version ?? "?",
                    date: when(last.at),
                  })
                : t("about.noUpgrade")}
            </dd>
          </div>
          <div className="row">
            <dt>{t("about.copies")}</dt>
            <dd>
              {data.pre_migration_copies.length > 0
                ? t("about.copiesKept", { count: data.pre_migration_copies.length })
                : t("about.noCopies")}
            </dd>
          </div>
          <div className="row">
            <dt>
              {t("about.latestBackup")}
              <span className="muted small">{t("about.backupHint")}</span>
            </dt>
            <dd>
              {backup
                ? t("about.backupLine", {
                    path: backup.path,
                    size: formatSize(backup.size, i18n.language),
                    date: when(backup.at),
                  })
                : t("about.noBackup")}
            </dd>
          </div>
        </dl>
      )}
    </section>
  );
}

/** A list written by the build (`licenses/*.json`), or null when the app has none: a
 * development run, where the server answers 404 for a missing file under `licenses/`. */
async function licenceList(file: string, signal: AbortSignal): Promise<LicenceRow[] | null> {
  let response: Response;
  try {
    response = await fetch(`/licenses/${file}`, { credentials: "same-origin", signal });
  } catch (error) {
    if (signal.aborted) throw error;
    return null;
  }
  if (!response.ok) return null;
  try {
    const body: unknown = await response.json();
    return Array.isArray(body) ? (body as LicenceRow[]) : null;
  } catch {
    return null;
  }
}

/** The third-party packages of the web app and of the server, each list in a native
 * <details>, closed at first; links to the full licence texts. */
function LicencesSection() {
  const { t } = useTranslation();
  return (
    <section className="card" aria-labelledby="about-licences">
      <h2 id="about-licences">{t("about.licences")}</h2>
      <p className="muted small">{t("about.licencesHint")}</p>
      <LicenceList
        id="licences-frontend"
        title={t("about.licencesFrontend")}
        list="frontend.json"
        texts="THIRD-PARTY.txt"
      />
      <LicenceList
        id="licences-backend"
        title={t("about.licencesBackend")}
        list="backend.json"
        texts="BACKEND-THIRD-PARTY.txt"
      />
    </section>
  );
}

function LicenceList(props: { id: string; title: string; list: string; texts: string }) {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ["licences", props.list],
    queryFn: ({ signal }) => licenceList(props.list, signal),
    staleTime: Infinity,
  });
  const rows = query.data;
  return (
    <div className="licence-list">
      <h3 id={props.id}>{props.title}</h3>
      {rows === null && <p className="muted small">{t("about.licencesInBuild")}</p>}
      {rows && (
        <>
          <p className="small">
            <a href={`/licenses/${props.texts}`}>{t("about.licencesTexts")}</a>
          </p>
          <details className="licence-details">
            <summary>{t("about.licencesShow", { count: rows.length })}</summary>
            <div className="table-scroll">
              <table className="data" aria-labelledby={props.id}>
                <thead>
                  <tr>
                    <th scope="col">{t("about.component")}</th>
                    <th scope="col">{t("about.componentVersion")}</th>
                    <th scope="col">{t("about.componentLicence")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={`${row.name}@${row.version}`}>
                      <td>{row.name}</td>
                      <td className="num">{row.version}</td>
                      <td>{row.license ?? t("about.licenceInText")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  );
}
