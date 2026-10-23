import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useAccounts, withAccount } from "../accounts";
import type { ImportConfirmOut, ImportPreview } from "../api/types";
import { formatDay } from "../format/date";
import { rowsText } from "../importText";
import { useAssets } from "../transactions";

type Props = {
  done: ImportConfirmOut;
  preview: ImportPreview | null; // the preview confirmed: names of new accounts, file rows
  onAnother: () => void;
};

type Batch = ImportConfirmOut["batches"][number];

/** Mockup 09: what the import added, per account when the file had several (D-142). */
export function ImportDone({ done, preview, onAnother }: Props) {
  const { t, i18n } = useTranslation();
  const accounts = useAccounts().data ?? [];
  const assets = useAssets().data; // read again after the import: it has the new securities
  const nameOf = (id: string) =>
    accounts.find((a) => a.id === id)?.name ??
    preview?.accounts.find((a) => a.account_id === id)?.name ??
    "—";
  const sum = (pick: (b: Batch) => number) =>
    done.batches.reduce((total, b) => total + pick(b), 0);
  const added = sum((b) => b.imported);
  const single = done.batches.length === 1 ? done.batches[0] : null;
  const rows = new Map(preview?.lines.map((l) => [l.key, l.lines]) ?? []);
  // As in the preview: the warnings this import brought; the earlier ones in one line.
  const introduced = done.warnings.filter((w) => w.introduced);
  const earlier = done.warnings.length - introduced.length;
  const figures = [
    ["accent", "added", added],
    ["", "duplicates", sum((b) => b.duplicates)],
    ["negative", "notImported", sum((b) => b.not_recognized + b.ambiguous + b.invalid)],
    ["warning", "warnings", introduced.length],
  ] as const;
  // Automatic prices are looked up only for a security with an ISIN; one without (a CSV
  // row may have none) has prices by hand only.
  const created = done.assets_created.map((id) => assets?.find((a) => a.id === id));
  const withIsin = created.filter((a) => a?.isin).length;
  const manual = created.filter((a) => a && !a.isin).length;
  const left = [
    ["toReview", sum((b) => b.to_review_skipped)],
    ["skipped", sum((b) => b.skipped_by_choice)],
  ] as const;

  return (
    <>
      <section className="card import-done">
        <h2>
          {single
            ? t("importDone.title", { count: added, account: nameOf(single.account_id) })
            : t("importDone.titleMany", { count: added, accounts: done.batches.length })}
        </h2>
        <dl className="import-counts">
          {figures.map(([tone, name, value]) => (
            <div key={name} className={tone ? `import-count import-count-${tone}` : "import-count"}>
              <dt>{t(`importDone.cards.${name}`)}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <ul className="plain-list import-done-notes">
          {left
            .filter(([, value]) => value > 0)
            .map(([name, value]) => (
              <li key={name}>{t(`importDone.${name}`, { count: value })}</li>
            ))}
          {done.accounts_created.length > 0 && (
            <li>{t("importDone.accountsCreated", { count: done.accounts_created.length })}</li>
          )}
          {withIsin > 0 && <li>{t("importDone.assetsCreated", { count: withIsin })}</li>}
          {manual > 0 && <li>{t("importDone.assetsManual", { count: manual })}</li>}
        </ul>
        {done.batches.length > 1 && (
          <div className="table-scroll">
            <table className="data">
              <caption className="visually-hidden">{t("importDone.perAccount")}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("importDone.account")}</th>
                  <th scope="col" className="num">
                    {t("importDone.cards.added")}
                  </th>
                  <th scope="col" className="num">
                    {t("importDone.cards.duplicates")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {done.batches.map((b) => (
                  <tr key={b.id}>
                    <td>{nameOf(b.account_id)}</td>
                    <td className="num">{b.imported}</td>
                    <td className="num">{b.duplicates}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="actions">
          <Link
            className="button button-primary"
            to={single ? withAccount("/transactions", single.account_id) : "/transactions"}
          >
            {t("importDone.toTransactions")}
          </Link>
          <button type="button" className="button" onClick={onAnother}>
            {t("importDone.another")}
          </button>
          <Link className="button" to={single ? withAccount("/", single.account_id) : "/"}>
            {t("importDone.toDashboard")}
          </Link>
        </div>
      </section>
      {(introduced.length > 0 || earlier > 0) && (
        <div className="notice-warning">
          <ul>
            {introduced.map((w, index) => {
              const where = w.line ? rows.get(w.line) : undefined;
              const account = preview?.accounts.find((a) => (a.key ?? null) === w.account);
              return (
                <li key={index}>
                  {t(`warnings.${w.code}`, {
                    account: account?.name ?? "",
                    date: formatDay(w.date, i18n.language),
                  })}
                  {where && ` (${rowsText(t, where)})`}
                </li>
              );
            })}
          </ul>
          {earlier > 0 && <p>{t("importPreview.earlierWarnings", { count: earlier })}</p>}
        </div>
      )}
    </>
  );
}
