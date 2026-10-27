import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useAccountInView } from "../accounts";
import type { Account, AssetShare, Breakdown, Figures, Share, StaleFx } from "../api/types";
import { GainsCard } from "../components/GainsCard";
import { PageTitle } from "../components/PageTitle";
import { Loading } from "../components/Status";
import {
  bestWorst,
  largest,
  type PriceNotice,
  priceNotices,
  uniquePairs,
  useBreakdown,
  useSummary,
} from "../dashboard";
import { formatDay, formatMonth } from "../format/date";
import { formatMoney, formatPercent, signOf } from "../format/decimal";
import { type HoldingRow, useHoldings } from "../holdings";
import { useMe } from "../session";
import { staleFxTexts } from "./Holdings";

const TONE = { "-1": "negative", "0": "", "1": "positive" } as const;

/** Mockup 01 without "Andamento nel tempo" (slice 7) and, until the charts come, with the
 * allocation and the income as tables (D-153). */
export default function Dashboard() {
  const { t } = useTranslation();
  const me = useMe().data;
  const { account, accounts, search, pending } = useAccountInView();
  const ready = !pending && accounts.length > 0;
  const summary = useSummary(account, ready);
  const holdings = useHoldings(account, ready);
  const breakdown = useBreakdown(account, ready);
  const marketData = me?.market_data ?? "ON";
  const name = me ? (me.display_name ?? me.username) : "";

  return (
    <>
      <PageTitle title={t("dashboard.heading")}>{t("dashboard.greeting", { name })}</PageTitle>
      {ready && summary.isPending && <Loading />}
      {summary.data && (
        <Figures
          figures={summary.data}
          account={account}
          accounts={accounts}
          rows={holdings.data?.rows ?? []}
          breakdown={breakdown.data}
          marketData={marketData}
          search={search}
        />
      )}
    </>
  );
}

type SummaryData = Figures & { stale_fx: StaleFx[]; unavailable_currencies?: string[] };

type FiguresProps = {
  figures: SummaryData;
  account: Account | null;
  accounts: Account[];
  rows: HoldingRow[];
  breakdown: Breakdown | undefined;
  marketData: "ON" | "OFF";
  search: string;
};

function Figures(props: FiguresProps) {
  const { figures, account, accounts, rows, breakdown, marketData, search } = props;
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const { currency } = figures;
  const money = (value: string, signed = false) => formatMoney(value, currency, locale, { signed });
  const date = formatDay(figures.day, locale);
  const converted = !account && accounts.some((a) => a.currency !== currency);

  return (
    <>
      <p className="muted">
        {account
          ? t("dashboard.viewOne", { name: account.name, currency, date })
          : t("dashboard.viewAll", { count: accounts.length, currency, date })}
      </p>
      {converted && <p className="muted small">{t("dashboard.fxNote")}</p>}

      <Banner
        notices={priceNotices(rows, marketData)}
        staleFx={uniquePairs(figures.stale_fx)}
        search={search}
      />

      <div className="dashboard-cards">
        <section className="card figure-card" aria-labelledby="dashboard-total">
          <h2 id="dashboard-total" className="card-label">
            {t("dashboard.totalValue")}
          </h2>
          <p className="figure num">{money(figures.total_value)}</p>
          <p className="muted small">
            {t("dashboard.totalLine", {
              securities: money(figures.securities_value),
              cash: money(figures.cash),
            })}
          </p>
        </section>
        <section className="card figure-card" aria-labelledby="dashboard-invested">
          <h2 id="dashboard-invested" className="card-label">
            {t("dashboard.invested")}
          </h2>
          <p className="figure num">{money(figures.invested_capital)}</p>
          <p className="muted small">{t("dashboard.investedLine")}</p>
        </section>
        <section className="card figure-card figure-card-accent" aria-labelledby="dashboard-gain">
          <h2 id="dashboard-gain" className="card-label">
            {t("dashboard.gainNet")}
          </h2>
          <p className="figure num">{money(figures.gain_net, true)}</p>
          <p className="small">
            {t("holding.gainLine", {
              gross: money(figures.gain_gross, true),
              taxes: money(figures.taxes),
            })}
          </p>
        </section>
      </div>

      <div className="dashboard-columns">
        <GainsCard
          gains={figures}
          currency={currency}
          incomeLabel={t("dashboard.incomeRow")}
          headingId="dashboard-gains"
        />
        <section className="card" aria-labelledby="dashboard-allocation">
          <h2 id="dashboard-allocation">{t("dashboard.allocationHeading")}</h2>
          {breakdown && <Allocation breakdown={breakdown} search={search} />}
        </section>
        <BestWorst rows={rows} search={search} />
        <section className="card" aria-labelledby="dashboard-income">
          <h2 id="dashboard-income">{t("dashboard.incomeHeading")}</h2>
          {breakdown && <Income breakdown={breakdown} />}
        </section>
      </div>
    </>
  );
}

type BannerProps = {
  notices: ReturnType<typeof priceNotices>;
  staleFx: StaleFx[];
  search: string;
};

/** What needs the user (D-153 (6)): each line of a security leads to its detail, where its
 * price notes and "enter a price by hand" are. */
function Banner({ notices, staleFx, search }: BannerProps) {
  const { t, i18n } = useTranslation();
  const { shown, more, pending } = notices;
  const lines = staleFxTexts(t, staleFx, i18n.language);
  return (
    <>
      {(shown.length > 0 || lines.length > 0) && (
        <ul className="warnings">
          {shown.map((notice) => (
            <li key={notice.row.asset_id}>
              <Link to={{ pathname: `/holdings/${notice.row.asset_id}`, search }}>
                {t("dashboard.noticeLine", { name: notice.row.name, text: noticeText(notice) })}
              </Link>
            </li>
          ))}
          {more > 0 && (
            <li>
              <Link to={{ pathname: "/holdings", search }}>
                {t("dashboard.noticeMore", { count: more })}
              </Link>
            </li>
          )}
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {pending > 0 && <p className="muted small">{t("dashboard.pending", { count: pending })}</p>}
    </>
  );

  function noticeText({ row, code, stale }: PriceNotice) {
    const parts = [];
    if (code) parts.push(t(`priceStatus.short.${code}`));
    if (stale && row.price_date) {
      parts.push(t(stale, { date: formatDay(row.price_date, i18n.language) }));
    }
    return parts.join(" · ");
  }
}

function Allocation({ breakdown, search }: { breakdown: Breakdown; search: string }) {
  const { t } = useTranslation();
  const typeName = (key: string) => (key === "CASH" ? t("dashboard.cash") : t(`assetTypes.${key}`));
  return (
    <>
      <ShareTable
        caption={t("dashboard.byType")}
        first={t("holdings.type")}
        parts={breakdown.by_type}
        currency={breakdown.currency}
        label={(part) => typeName(part.key)}
      />
      <ShareTable
        caption={t("dashboard.byCurrency")}
        first={t("holdings.currency")}
        parts={breakdown.by_currency}
        currency={breakdown.currency}
        label={(part) => part.key}
      />
      <AssetTable parts={breakdown.by_asset} currency={breakdown.currency} search={search} />
    </>
  );
}

type ShareTableProps = {
  caption: string;
  first: string;
  parts: Share[];
  currency: string;
  label: (part: Share) => string;
};

function ShareTable({ caption, first, parts, currency, label }: ShareTableProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  return (
    <table className="data share-table">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">{first}</th>
          <th scope="col" className="num">
            {t("accounts.value")}
          </th>
          <th scope="col" className="num">
            {t("dashboard.share")}
          </th>
        </tr>
      </thead>
      <tbody>
        {parts.map((part) => (
          <tr key={part.key}>
            <th scope="row">{label(part)}</th>
            <td className="num">{formatMoney(part.value, currency, locale)}</td>
            <td className="num">{formatPercent(part.share, locale)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type AssetTableProps = { parts: AssetShare[]; currency: string; search: string };

/** The 10 largest securities; the others on request, never summed in the browser. */
function AssetTable({ parts, currency, search }: AssetTableProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const [all, setAll] = useState(false);
  const firstHidden = useRef<HTMLTableCellElement>(null);
  const { shown, rest } = largest(parts);

  // "Altri" goes away with the click: the focus goes to the first security it showed.
  useEffect(() => {
    if (all) firstHidden.current?.focus();
  }, [all]);

  return (
    <table className="data share-table">
      <caption>{t("dashboard.byAsset")}</caption>
      <thead>
        <tr>
          <th scope="col">{t("holdings.asset")}</th>
          <th scope="col" className="num">
            {t("accounts.value")}
          </th>
          <th scope="col" className="num">
            {t("dashboard.share")}
          </th>
        </tr>
      </thead>
      <tbody>
        {(all ? parts : shown).map((part, i) => (
          <tr key={part.asset_id}>
            <th
              scope="row"
              ref={i === shown.length ? firstHidden : undefined}
              tabIndex={i === shown.length ? -1 : undefined}
            >
              <Link to={{ pathname: `/holdings/${part.asset_id}`, search }}>{part.name}</Link>
            </th>
            <td className="num">{formatMoney(part.value, currency, locale)}</td>
            <td className="num">{formatPercent(part.share, locale)}</td>
          </tr>
        ))}
        {!all && rest.length > 0 && (
          <tr>
            <th scope="row" colSpan={3}>
              <button
                type="button"
                className="link-button"
                aria-expanded={false}
                onClick={() => setAll(true)}
              >
                {t("dashboard.others", { count: rest.length })}
              </button>
            </th>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function BestWorst({ rows, search }: { rows: HoldingRow[]; search: string }) {
  const { t, i18n } = useTranslation();
  const { ranked } = bestWorst(rows);
  return (
    <section className="card" aria-labelledby="dashboard-best">
      <h2 id="dashboard-best">{t("dashboard.bestWorstHeading")}</h2>
      <p className="muted small">{t("dashboard.bestWorstIntro")}</p>
      <ul className="ranked-list">
        {ranked.map((row) => {
          const pct = row.unrealized_pct as string;
          const tone = TONE[signOf(pct)];
          return (
            <li key={row.asset_id}>
              <span>
                <Link
                  to={{ pathname: `/holdings/${row.asset_id}`, search }}
                  className="strong block"
                >
                  {row.name}
                </Link>
                <span className="muted small">
                  {t(`assetTypes.${row.asset_type}`)} · {row.quote_currency}
                </span>
              </span>
              <span className={`gain-pill ${tone}`}>
                {formatPercent(pct, i18n.language, { signed: true })}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Income({ breakdown }: { breakdown: Breakdown }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const money = (value: string) => formatMoney(value, breakdown.currency, locale);
  const total = breakdown.income_total;
  const head = (first: string) => (
    <thead>
      <tr>
        <th scope="col">{first}</th>
        <th scope="col" className="num">
          {t("holding.gross")}
        </th>
        <th scope="col" className="num">
          {t("holding.net")}
        </th>
      </tr>
    </thead>
  );
  return (
    <>
      <table className="data share-table">
        <caption>{t("dashboard.incomeIntro")}</caption>
        {head(t("dashboard.month"))}
        <tbody>
          {breakdown.income_by_month.map((m) => (
            <tr key={m.month}>
              <th scope="row">{formatMonth(m.month, locale)}</th>
              <td className="num">{money(m.gross)}</td>
              <td className="num">{money(m.net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="small">
        {t("dashboard.incomeSince", { gross: money(total.gross), net: money(total.net) })}
      </p>
      <table className="data share-table">
        <caption>{t("dashboard.incomeByYear")}</caption>
        {head(t("dashboard.year"))}
        <tbody>
          {breakdown.income_by_year.map((y) => (
            <tr key={y.year}>
              <th scope="row">{y.year}</th>
              <td className="num">{money(y.gross)}</td>
              <td className="num">{money(y.net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{t("dashboard.incomeAccrued")}</p>
    </>
  );
}
