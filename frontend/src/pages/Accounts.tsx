import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useAccountInView, useAccounts, useAccountSummary, withAccount } from "../accounts";
import type { Account, AccountSummary } from "../api/types";
import { Notice, useNotice } from "../components/Notice";
import { PageTitle } from "../components/PageTitle";
import { LoadError, Loading } from "../components/Status";
import { formatDay } from "../format/date";
import { formatMoney, signOf } from "../format/decimal";
import { useMe } from "../session";
import { AccountFormDialog, DeleteAccountDialog } from "./AccountDialogs";

type Open = { kind: "new" } | { kind: "edit" | "delete"; account: Account } | null;

/** Mockup 13: the user's accounts with their figures; "Apri" sets the account in view. */
export default function Accounts() {
  const { t } = useTranslation();
  const reporting = useMe().data?.reporting_currency ?? "";
  const accounts = useAccounts();
  const { account: inView, setAccount } = useAccountInView();
  const [open, setOpen] = useState<Open>(null);
  const { notice, announce: say } = useNotice();
  // The account just created, for the link to add what it already holds (D-147).
  const [createdId, setCreatedId] = useState<string | null>(null);
  const announce = (text: string, created: string | null = null) => {
    setCreatedId(created);
    say(text);
  };
  const noticeRef = useRef<HTMLDivElement>(null);
  const deletedId = useRef<string | null>(null);
  const list = accounts.data;

  // After a deletion the dialog and the card are gone: the focus goes to the confirmation.
  useEffect(() => {
    const id = deletedId.current;
    if (id && open === null && !list?.some((a) => a.id === id)) {
      deletedId.current = null;
      noticeRef.current?.focus();
    }
  }, [list, open]);

  const create = () => setOpen({ kind: "new" });
  const hasAccounts = list !== undefined && list.length > 0;

  return (
    <>
      <div className="page-head">
        <div>
          <PageTitle title={t("accounts.heading")} />
          {hasAccounts && (
            <p className="muted">{t("accounts.intro", { count: list.length })}</p>
          )}
        </div>
        {hasAccounts && (
          <button type="button" className="button button-primary" onClick={create}>
            {t("accounts.new")}
          </button>
        )}
      </div>

      <Notice notice={notice} ref={noticeRef} />
      {createdId && list?.some((a) => a.id === createdId) && (
        <p className="next-links account-next">
          <Link to={withAccount("/holdings/existing", createdId)}>{t("accounts.addHoldingsLink")}</Link>
        </p>
      )}

      {accounts.isPending && <Loading />}
      {accounts.isError && (
        <LoadError error={accounts.error} onRetry={() => void accounts.refetch()} />
      )}
      {list?.length === 0 && (
        <section className="card empty-state">
          <h2>{t("accounts.emptyHeading")}</h2>
          <p className="muted">{t("accounts.emptyText")}</p>
          <button type="button" className="button button-primary" onClick={create}>
            {t("accounts.createFirst")}
          </button>
        </section>
      )}
      <div className="stack">
        {list?.map((account) => (
          <AccountCard
            key={account.id}
            account={account}
            reporting={reporting}
            onEdit={() => setOpen({ kind: "edit", account })}
          />
        ))}
      </div>

      {open?.kind === "new" && (
        <AccountFormDialog
          defaultCurrency={reporting}
          onClose={() => setOpen(null)}
          onSaved={(saved) => {
            setOpen(null);
            announce(t("accounts.created", { name: saved.name }), saved.id);
          }}
        />
      )}
      {open?.kind === "edit" && (
        <AccountFormDialog
          account={open.account}
          defaultCurrency={reporting}
          onClose={() => setOpen(null)}
          onSaved={(saved) => {
            setOpen(null);
            announce(t("accounts.saved", { name: saved.name }));
          }}
          onDelete={() => setOpen({ kind: "delete", account: open.account })}
        />
      )}
      {open?.kind === "delete" && (
        <DeleteAccountDialog
          account={open.account}
          onClose={() => setOpen(null)}
          onDeleted={(count) => {
            deletedId.current = open.account.id;
            if (inView?.id === open.account.id) setAccount(null);
            setOpen(null);
            announce(t("accounts.deleted", { name: open.account.name, count }));
          }}
        />
      )}
    </>
  );
}

function AccountCard({
  account,
  reporting,
  onEdit,
}: {
  account: Account;
  reporting: string;
  onEdit: () => void;
}) {
  const { t } = useTranslation();
  const summary = useAccountSummary(account.id);
  const headingId = useId();
  const other = account.currency !== reporting;

  return (
    <section className="card account-card" aria-labelledby={headingId}>
      <div className="account-head">
        <div>
          <h2 id={headingId}>{account.name}</h2>
          <ul className="chips">
            <li className="chip chip-accent">{account.currency}</li>
            {account.provider && <li className="chip">{account.provider}</li>}
            <li className="chip">
              {other
                ? t("accounts.valuesInAlso", { currency: account.currency, reporting })
                : t("accounts.valuesIn", { currency: account.currency })}
            </li>
          </ul>
        </div>
        <div className="actions">
          {/* The card's heading tells the buttons of different cards apart. */}
          <Link
            to={withAccount("/", account.id)}
            className="button button-primary"
            aria-describedby={headingId}
          >
            {t("accounts.open")}
          </Link>
          <button type="button" className="button" onClick={onEdit} aria-describedby={headingId}>
            {t("accounts.edit")}
          </button>
        </div>
      </div>
      {summary.isPending && <Loading />}
      {summary.isError && (
        <LoadError error={summary.error} onRetry={() => void summary.refetch()} />
      )}
      {summary.data && <Figures summary={summary.data} showReporting={other} />}
    </section>
  );
}

const TONE = { "-1": "negative", "0": "", "1": "positive" } as const;

/** The account's figures in its currency (D-36), with partial and stale-rate notices (D-64,
 * D-120); its value in the reporting currency when that differs. */
function Figures({ summary, showReporting }: { summary: AccountSummary; showReporting: boolean }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const money = (value: string, signed = false) =>
    formatMoney(value, summary.currency, locale, { signed });
  const { reporting } = summary;

  const notes: string[] = [];
  if (summary.partial) notes.push(t("accounts.partialNote"));
  for (const fx of summary.stale_fx) {
    notes.push(
      t("accounts.staleFx", {
        pair: `${fx.from_currency}/${fx.to_currency}`,
        date: formatDay(fx.rate_date, locale),
      }),
    );
  }

  let reportingLine: string | null = null;
  if (showReporting) {
    if (reporting.total_value === null) {
      notes.push(t("accounts.reportingMissing", { currency: reporting.currency }));
    } else {
      const value = formatMoney(reporting.total_value, reporting.currency, locale);
      reportingLine = reporting.stale_fx
        ? t("accounts.reportingStale", {
            currency: reporting.currency,
            date: formatDay(reporting.stale_fx.rate_date, locale),
            value,
          })
        : t("accounts.reportingValue", { currency: reporting.currency, value });
    }
  }

  return (
    <>
      <dl className="figures">
        <div>
          <dt>{t("accounts.value")}</dt>
          <dd className="figure num">
            {money(summary.total_value)}
            {summary.partial && <span className="pill pill-warning">{t("accounts.partial")}</span>}
          </dd>
        </div>
        <div>
          <dt>{t("accounts.cash")}</dt>
          <dd className="figure num">{money(summary.cash)}</dd>
        </div>
        <div>
          <dt>{t("accounts.netGain")}</dt>
          <dd className={`figure num ${TONE[signOf(summary.gain_net)]}`}>
            {money(summary.gain_net, true)}
          </dd>
        </div>
        <div>
          <dt>{t("accounts.holdings")}</dt>
          <dd className="figure num">{summary.open_positions}</dd>
        </div>
      </dl>
      {reportingLine && <p className="muted small reporting-line">{reportingLine}</p>}
      {notes.length > 0 && (
        <ul className="warnings">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </>
  );
}
