import { type UseQueryResult, useQueryClient } from "@tanstack/react-query";
import { lazy, type ReactNode, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { ACCOUNTS_KEY, useAccountInView, useAccounts } from "../accounts";
import { ApiError } from "../api/client";
import type { Account, AssetShare, Breakdown, Figures, Share, StaleFx } from "../api/types";
import { type ChartTheme, monthBars, shareBar, shareDonut, swatch } from "../chartOptions";
import type { ChartProps } from "../charts/Chart";
import { ChartGuard } from "../components/ChartGuard";
import { GainsCard, TONE } from "../components/GainsCard";
import { PageTitle } from "../components/PageTitle";
import { LoadError, Loading } from "../components/Status";
import {
  bestWorst,
  largest,
  type PriceNotice,
  priceNotices,
  uniquePairs,
  useBreakdown,
  useSummary,
} from "../dashboard";
import { formatDay, formatMonth, formatMonthShort } from "../format/date";
import { formatMoney, formatPercent, signOf } from "../format/decimal";
import { type HoldingRow, type HoldingsView, useHoldings } from "../holdings";
import { useMe } from "../session";
import { staleFxTexts } from "./Holdings";

// The charts and their library come in a lazy chunk of their own (D-138), after the figures.
const LazyChart = lazy(() => import("../charts/Chart"));

/** A chart in the user's theme; nothing while its chunk loads: the table beside it is there. */
function DrawnChart(props: Omit<ChartProps, "theme">) {
  const theme = useMe().data?.theme;
  return (
    <ChartGuard>
      <Suspense fallback={null}>
        <LazyChart {...props} theme={theme} />
      </Suspense>
    </ChartGuard>
  );
}

/** Mockup 01 without "Andamento nel tempo" (slice 7) and, until the charts come, with the
 * allocation and the income as tables (D-153). */
export default function Dashboard() {
  const { t } = useTranslation();
  const me = useMe().data;
  const list = useAccounts();
  const { account, accounts, search, pending } = useAccountInView();
  const loaded = !pending && list.data !== undefined;
  // Nothing to show before the first account, or while the view has no transaction: no
  // figures are asked for then.
  const empty = loaded && (account ? [account] : accounts).every((a) => a.transaction_count === 0);
  const ready = loaded && !empty;
  const summary = useSummary(account, ready);
  const holdings = useHoldings(account, ready);
  const breakdown = useBreakdown(account, ready);
  const marketData = me?.market_data ?? "ON";
  const name = me ? (me.display_name ?? me.username) : "";
  const client = useQueryClient();
  const gone = summary.error instanceof ApiError && summary.error.code === "NOT_FOUND";

  // The account in view was deleted meanwhile (another tab): with the list read again it is no
  // longer found, and the view falls back to all accounts.
  useEffect(() => {
    if (gone) void client.invalidateQueries({ queryKey: ACCOUNTS_KEY, exact: true });
  }, [gone, client]);

  return (
    <>
      <PageTitle title={t("dashboard.heading")}>{t("dashboard.greeting", { name })}</PageTitle>
      {list.isError && <LoadError error={list.error} onRetry={() => void list.refetch()} />}
      {!list.isError && (pending || list.isPending || (ready && summary.isPending)) && <Loading />}
      {empty && <Empty noAccounts={accounts.length === 0} search={search} />}
      {ready && summary.isError && (
        <LoadError error={summary.error} onRetry={() => void summary.refetch()} />
      )}
      {ready && summary.data && (
        <ViewFigures
          key={account?.id ?? "all"}
          figures={summary.data}
          account={account}
          accounts={accounts}
          holdings={holdings}
          breakdown={breakdown}
          marketData={marketData}
          search={search}
        />
      )}
    </>
  );
}

function Empty({ noAccounts, search }: { noAccounts: boolean; search: string }) {
  const { t } = useTranslation();
  if (noAccounts) {
    return (
      <section className="card empty-state">
        <p className="muted">{t("dashboard.emptyNoAccounts")}</p>
        <Link to={{ pathname: "/accounts", search }} className="button button-primary">
          {t("transactions.toAccounts")}
        </Link>
      </section>
    );
  }
  return (
    <section className="card empty-state">
      <p className="muted">{t("dashboard.emptyNoHoldings")}</p>
      <div className="actions">
        <Link to={{ pathname: "/holdings/existing", search }} className="button">
          {t("holdings.addExisting")}
        </Link>
        <Link to={{ pathname: "/holdings/add", search }} className="button">
          {t("holdings.addAsset")}
        </Link>
        <Link to={{ pathname: "/import", search }} className="button">
          {t("nav.import")}
        </Link>
      </div>
    </section>
  );
}

type PartProps<T> = { query: UseQueryResult<T>; children: (data: T) => ReactNode };

/** A card's own answer: its figures, else its error with "Riprova", else loading. A failed
 * card leaves the others as they are. */
function Part<T>({ query, children }: PartProps<T>) {
  if (query.data !== undefined) return children(query.data);
  if (query.isError) return <LoadError error={query.error} onRetry={() => void query.refetch()} />;
  return <Loading />;
}

type SummaryData = Figures & { stale_fx: StaleFx[]; unavailable_currencies?: string[] };

type ViewFiguresProps = {
  figures: SummaryData;
  account: Account | null;
  accounts: Account[];
  holdings: UseQueryResult<HoldingsView>;
  breakdown: UseQueryResult<Breakdown>;
  marketData: "ON" | "OFF";
  search: string;
};

function ViewFigures(props: ViewFiguresProps) {
  const { figures, account, accounts, holdings, breakdown, marketData, search } = props;
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const { currency, partial } = figures;
  const leftOut = figures.unavailable_currencies ?? [];
  const money = (value: string, signed = false) => formatMoney(value, currency, locale, { signed });
  const date = formatDay(figures.day, locale);
  const converted = !account && accounts.some((a) => a.currency !== currency);
  const pill = <span className="pill pill-warning">{t("accounts.partial")}</span>;

  return (
    <>
      <p className="muted">
        {account
          ? t("dashboard.viewOne", { name: account.name, currency, date })
          : t("dashboard.viewAll", { count: accounts.length, currency, date })}
      </p>
      {converted && <p className="muted small">{t("dashboard.fxNote")}</p>}

      <Banner
        notices={priceNotices(holdings.data?.rows ?? [], marketData)}
        staleFx={uniquePairs(figures.stale_fx)}
        partial={partial}
        leftOut={leftOut}
        currency={currency}
        search={search}
      />

      <div className="dashboard-cards">
        <section className="card figure-card" aria-labelledby="dashboard-total">
          <h2 id="dashboard-total" className="card-label">
            {t("dashboard.totalValue")}
          </h2>
          <p className="figure num">
            {money(figures.total_value)}
            {partial && pill}
          </p>
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
          <p className="figure num">
            {money(figures.invested_capital)}
            {/* Only an account left out takes cost away (D-73): a security without a rate
                is still at its cost in its account's currency. */}
            {leftOut.length > 0 && pill}
          </p>
          <p className="muted small">{t("dashboard.investedLine")}</p>
        </section>
        <section className="card figure-card figure-card-accent" aria-labelledby="dashboard-gain">
          <h2 id="dashboard-gain" className="card-label">
            {t("dashboard.gainNet")}
          </h2>
          <p className="figure num">
            {money(figures.gain_net, true)}
            {partial && pill}
          </p>
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
          partial={partial}
        />
        <section className="card" aria-labelledby="dashboard-allocation">
          <h2 id="dashboard-allocation">{t("dashboard.allocationHeading")}</h2>
          <Part query={breakdown}>{(data) => <Allocation breakdown={data} search={search} />}</Part>
        </section>
        <section className="card" aria-labelledby="dashboard-best">
          <h2 id="dashboard-best">{t("dashboard.bestWorstHeading")}</h2>
          <p className="muted small">{t("dashboard.bestWorstIntro")}</p>
          <Part query={holdings}>{(data) => <BestWorst rows={data.rows} search={search} />}</Part>
        </section>
        <section className="card" aria-labelledby="dashboard-income">
          <h2 id="dashboard-income">{t("dashboard.incomeHeading")}</h2>
          <Part query={breakdown}>
            {(data) => (
              <>
                <LeftOut breakdown={data} />
                <Income breakdown={data} />
              </>
            )}
          </Part>
          <p className="muted small">{t("dashboard.incomeAccrued")}</p>
        </section>
      </div>
    </>
  );
}

type BannerProps = {
  notices: ReturnType<typeof priceNotices>;
  staleFx: StaleFx[];
  partial: boolean;
  leftOut: string[]; // account currencies without a rate (D-73)
  currency: string;
  search: string;
};

/** What needs the user (D-153 (6)): each line of a security leads to its detail, where its
 * price notes and "enter a price by hand" are. */
function Banner({ notices, staleFx, partial, leftOut, currency, search }: BannerProps) {
  const { t, i18n } = useTranslation();
  const { shown, more, pending, off } = notices;
  const lines = staleFxTexts(t, staleFx, i18n.language);
  if (partial) lines.unshift(t("accounts.partialNote"));
  if (leftOut.length > 0) {
    lines.splice(1, 0, t("holdings.leftOut", { currencies: leftOut.join(", "), currency }));
  }
  return (
    <>
      {off && <p className="notice">{t("holdings.pricesOff")}</p>}
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
  const { t, i18n } = useTranslation();
  const typeName = (key: string) => (key === "CASH" ? t("dashboard.cash") : t(`assetTypes.${key}`));
  const { by_type: types, by_currency: currencies } = breakdown;
  const donut = useCallback(
    (theme: ChartTheme, animation: boolean) => shareDonut(types, theme, animation),
    [types],
  );
  const bar = useCallback(
    (theme: ChartTheme, animation: boolean) => shareBar(currencies, theme, animation),
    [currencies],
  );
  return (
    <>
      <div className="chart-pair">
        {types.length > 0 && <DrawnChart build={donut} className="chart-donut" />}
        <ShareTable
          caption={t("dashboard.byType")}
          first={t("holdings.type")}
          parts={types}
          currency={breakdown.currency}
          label={(part) => typeName(part.key)}
        />
      </div>
      <ShareTable
        caption={t("dashboard.byCurrency")}
        first={t("holdings.currency")}
        parts={currencies}
        currency={breakdown.currency}
        label={(part) => part.key}
        chart={currencies.length > 0 && <DrawnChart build={bar} className="chart-bar" />}
      />
      <AssetTable parts={breakdown.by_asset} currency={breakdown.currency} search={search} />
      {signOf(breakdown.negative_cash) < 0 && (
        <p className="muted small">
          {t("dashboard.negativeCash", {
            amount: formatMoney(breakdown.negative_cash, breakdown.currency, i18n.language),
          })}
        </p>
      )}
      <LeftOut breakdown={breakdown} />
      {/* Accounts left out are said above; otherwise a security without a rate. */}
      {breakdown.partial && breakdown.unavailable_currencies.length === 0 && (
        <p className="muted small">{t("dashboard.allocationPartial")}</p>
      )}
    </>
  );
}

type ShareTableProps = {
  caption: string;
  first: string;
  parts: Share[];
  currency: string;
  label: (part: Share) => string;
  chart?: ReactNode; // drawn between the caption and the table
};

function ShareTable({ caption, first, parts, currency, label, chart }: ShareTableProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  // With a chart, the caption is shown above the chart and read with the table.
  const table = (
    <table className="data share-table">
      <caption className={chart ? "visually-hidden" : undefined}>{caption}</caption>
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
        {parts.map((part, i) => (
          <tr key={part.key}>
            <th scope="row">
              <span className={swatch(i)} aria-hidden="true" />
              {label(part)}
            </th>
            <td className="num">{formatMoney(part.value, currency, locale)}</td>
            <td className="num">{formatPercent(part.share, locale)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  if (!chart) return table;
  return (
    <div className="chart-stack">
      <p className="chart-caption" aria-hidden="true">
        {caption}
      </p>
      {chart}
      {table}
    </div>
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
  const { ranked, atCost } = bestWorst(rows);
  return (
    <>
      {ranked.length < 2 ? (
        <p>{t("dashboard.bestWorstNone")}</p>
      ) : (
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
      )}
      {atCost > 0 && (
        <p className="muted small">{t("dashboard.bestWorstAtCost", { count: atCost })}</p>
      )}
    </>
  );
}

function Income({ breakdown }: { breakdown: Breakdown }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const money = (value: string) => formatMoney(value, breakdown.currency, locale);
  const total = breakdown.income_total;
  // The months are gross, as their caption says; the years and the total also net.
  const head = (first: string, net: boolean) => (
    <thead>
      <tr>
        <th scope="col">{first}</th>
        <th scope="col" className="num">
          {t("holding.gross")}
        </th>
        {net && (
          <th scope="col" className="num">
            {t("holding.net")}
          </th>
        )}
      </tr>
    </thead>
  );
  const months = breakdown.income_by_month;
  const bars = useCallback(
    (theme: ChartTheme, animation: boolean) =>
      monthBars(
        months.map((m) => ({ label: formatMonthShort(m.month, locale), value: m.gross })),
        theme,
        animation,
      ),
    [months, locale],
  );
  const drawn = months.some((m) => signOf(m.gross) !== 0);
  // No dividend or coupon ever: the months would all be zero. With an account left out its
  // income is unknown, so "none" is never said then.
  if (breakdown.income_by_year.length === 0) {
    if (breakdown.unavailable_currencies.length > 0) return null;
    return <p>{t("dashboard.incomeNone")}</p>;
  }
  return (
    <>
      {/* No bar to draw when no month of the twelve has income: the table alone, then. */}
      {drawn && (
        <>
          <p className="chart-caption" aria-hidden="true">
            {t("dashboard.incomeIntro")}
          </p>
          <DrawnChart build={bars} className="chart-months" />
        </>
      )}
      <table className="data share-table">
        <caption className={drawn ? "visually-hidden" : undefined}>
          {t("dashboard.incomeIntro")}
        </caption>
        {head(t("dashboard.month"), false)}
        <tbody>
          {months.map((m) => (
            <tr key={m.month}>
              <th scope="row">{formatMonth(m.month, locale)}</th>
              <td className="num">{money(m.gross)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="small">
        {t("dashboard.incomeSince", { gross: money(total.gross), net: money(total.net) })}
      </p>
      <table className="data share-table">
        <caption>{t("dashboard.incomeByYear")}</caption>
        {head(t("dashboard.year"), true)}
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
    </>
  );
}

/** The accounts left out of a breakdown, said in each card that misses their figures (D-73). */
function LeftOut({ breakdown }: { breakdown: Breakdown }) {
  const { t } = useTranslation();
  const currencies = breakdown.unavailable_currencies;
  if (currencies.length === 0) return null;
  return (
    <p className="muted small">
      {t("holdings.leftOut", { currencies: currencies.join(", "), currency: breakdown.currency })}
    </p>
  );
}
