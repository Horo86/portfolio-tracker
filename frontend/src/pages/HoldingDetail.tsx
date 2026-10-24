import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { useAccountInView } from "../accounts";
import type { Account, Asset } from "../api/types";
import { Notice, useSavedNotice } from "../components/Notice";
import { PageTitle } from "../components/PageTitle";
import { PriceNotes } from "../components/PriceStatus";
import { LoadError, Loading } from "../components/Status";
import { formatDay } from "../format/date";
import {
  cashEffect,
  formatMoney,
  formatPercent,
  formatQuantity,
  signOf,
} from "../format/decimal";
import { type HoldingRow, useHoldings } from "../holdings";
import { useMe } from "../session";
import { afterChange, PAGE_SIZE, useAssets, useTransactions } from "../transactions";
import { averageCost, quotePrice, staleFxTexts } from "./Holdings";
import { DeleteAllDialog } from "./TransactionBulk";
import { useWarningTexts } from "./TransactionDialogs";

const TONE = { "-1": "negative", "0": "", "1": "positive" } as const;
const tone = (value: string | null) => (value === null ? "" : TONE[signOf(value)]);

/** Mockup 03 without the price chart (slice 7): one holding in the account in view, or over
 * all accounts (D-143, D-146), with its gains, income and transactions. */
export default function HoldingDetail() {
  const { t } = useTranslation();
  const { assetId = "" } = useParams();
  const { account, accounts, search, pending } = useAccountInView();
  const holdings = useHoldings(account, !pending);
  const assets = useAssets();
  const { notice } = useSavedNotice();

  const row = holdings.data?.rows.find((r) => r.asset_id === assetId);
  const asset = assets.data?.find((a) => a.id === assetId);
  const back = (
    <Link to={{ pathname: "/holdings", search }} className="back-link">
      {t("holding.back")}
    </Link>
  );

  if (pending || holdings.isPending || assets.isPending) return <Loading />;
  if (holdings.isError) {
    return <LoadError error={holdings.error} onRetry={() => void holdings.refetch()} />;
  }
  if (!row) {
    return (
      <>
        {back}
        <PageTitle title={asset?.name ?? t("holding.notFoundHeading")} />
        <section className="card empty-state">
          {asset && account ? (
            <>
              <p className="muted">{t("holding.notHere", { account: account.name })}</p>
              <Link to={`/holdings/${asset.id}`} className="button">
                {t("holding.toAllAccounts")}
              </Link>
            </>
          ) : (
            <p className="muted">{t(asset ? "holding.noTransactions" : "holding.notFound")}</p>
          )}
        </section>
      </>
    );
  }
  return (
    <Detail
      row={row}
      asset={asset}
      currency={holdings.data.currency}
      account={account}
      accounts={accounts}
      search={search}
      back={back}
      notice={notice}
    />
  );
}

type DetailProps = {
  row: HoldingRow;
  asset: Asset | undefined;
  currency: string;
  account: Account | null;
  accounts: Account[];
  search: string;
  back: ReactNode;
  notice: ReturnType<typeof useSavedNotice>["notice"];
};

function Detail({ row, asset, currency, account, accounts, search, back, notice }: DetailProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const location = useLocation();
  const marketData = useMe().data?.market_data ?? "ON";
  const money = (value: string, signed = false) => formatMoney(value, currency, locale, { signed });
  const percent = (value: string) => formatPercent(value, locale, { signed: true });
  const accountById = new Map(accounts.map((a) => [a.id, a]));
  const bond = row.asset_type === "BOND";
  const here = { backPath: location.pathname, back: search };

  return (
    <>
      {back}
      <div className="page-head">
        <div>
          <PageTitle title={row.name} />
          <ul className="chips" aria-label={t("holding.facts")}>
            <li className="chip">{t(`assetTypes.${row.asset_type}`)}</li>
            <li className="chip">{row.quote_currency}</li>
            {row.isin && <li className="chip">{row.isin}</li>}
            {row.ticker && <li className="chip">{row.ticker}</li>}
            {row.account_ids.map((id) => (
              <li key={id} className="chip chip-accent">
                {accountById.get(id)?.name ?? "—"}
              </li>
            ))}
          </ul>
        </div>
        {(account || accounts.length > 0) && (
          <div className="actions head-actions">
            <Link
              to={{
                pathname: "/transactions/new",
                search: `${search ? `${search}&` : "?"}asset=${row.asset_id}`,
              }}
              state={here}
              className="button button-primary"
            >
              {t("holding.addTransaction")}
            </Link>
          </div>
        )}
      </div>

      <Notice notice={notice} />
      <PriceNotes holding={row} marketData={marketData} form="long" showOk />
      {(row.partial || row.stale_fx.length > 0) && (
        <ul className="warnings">
          {row.partial && <li>{t("accounts.partialNote")}</li>}
          {staleFxTexts(t, row.stale_fx, locale).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}

      <div className="holding-cards">
        <section className="card figure-card" aria-labelledby="holding-value">
          <h2 id="holding-value" className="card-label">
            {t("holding.value")}
          </h2>
          <p className="figure num">{row.value === null ? "—" : money(row.value)}</p>
          <p className="muted small">
            {row.price && row.price_date
              ? t(bond ? "holding.valueLineBond" : "holding.valueLine", {
                  quantity: formatQuantity(row.quantity, locale),
                  price: quotePrice(t, row, row.price, locale),
                  date: formatDay(row.price_date, locale),
                })
              : t("holding.quantityLine", { quantity: formatQuantity(row.quantity, locale) })}
          </p>
        </section>
        <section className="card figure-card" aria-labelledby="holding-cost">
          <h2 id="holding-cost" className="card-label">
            {t("holding.cost")}
          </h2>
          <p className="figure num">{money(row.cost_basis)}</p>
          {row.pmc !== null && (
            <p className="muted small">
              {t("holding.pmcLine", { pmc: averageCost(t, row, currency, locale) })}
            </p>
          )}
        </section>
        <section className="card figure-card figure-card-accent" aria-labelledby="holding-gain">
          <h2 id="holding-gain" className="card-label">
            {t("holding.gainNet")}
          </h2>
          <p className="figure num">{row.gain_net === null ? "—" : money(row.gain_net, true)}</p>
          {row.gain_gross !== null && (
            <p className="small">
              {t("holding.gainLine", { gross: money(row.gain_gross, true), taxes: money(row.taxes) })}
            </p>
          )}
        </section>
      </div>

      <div className="holding-columns">
        <section className="card" aria-labelledby="holding-gains">
          <h2 id="holding-gains">{t("holding.gainsHeading")}</h2>
          <p className="muted small">{t("holding.gainsIntro")}</p>
          <table className="data gain-table">
            <thead>
              <tr>
                <th scope="col">{t("holding.item")}</th>
                <th scope="col" className="num">
                  {t("holding.gross")}
                </th>
                <th scope="col" className="num">
                  {t("holding.net")}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">
                  {t("holding.unrealized")}
                  {row.unrealized_pct !== null && (
                    <span className="muted small block">
                      {t("holding.unrealizedPct", { value: percent(row.unrealized_pct) })}
                    </span>
                  )}
                </th>
                <td className={`num ${tone(row.unrealized)}`}>
                  {row.unrealized === null ? "—" : money(row.unrealized, true)}
                </td>
                <td className={`num ${tone(row.unrealized)}`}>
                  {row.unrealized === null ? "—" : money(row.unrealized, true)}
                </td>
              </tr>
              <tr>
                <th scope="row">
                  {t("holding.realized")}
                  {row.realized_pct_gross !== null && row.realized_pct_net !== null && (
                    <span className="muted small block">
                      {t("holding.realizedPct", {
                        gross: percent(row.realized_pct_gross),
                        net: percent(row.realized_pct_net),
                      })}
                    </span>
                  )}
                </th>
                <td className={`num ${tone(row.realized_gross)}`}>
                  {money(row.realized_gross, true)}
                </td>
                <td className={`num ${tone(row.realized_net)}`}>{money(row.realized_net, true)}</td>
              </tr>
              <tr>
                <th scope="row">
                  {t(bond ? "holding.coupons" : "holding.dividends")}
                  <span className="muted small block">{t("holding.incomeNoPct")}</span>
                </th>
                <td className={`num ${tone(row.income_gross)}`}>{money(row.income_gross, true)}</td>
                <td className={`num ${tone(row.income_net)}`}>{money(row.income_net, true)}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">{t("holding.total")}</th>
                <td className={`num ${tone(row.gain_gross)}`}>
                  {row.gain_gross === null ? "—" : money(row.gain_gross, true)}
                </td>
                <td className={`num strong ${tone(row.gain_net)}`}>
                  {row.gain_net === null ? "—" : money(row.gain_net, true)}
                </td>
              </tr>
            </tfoot>
          </table>
          <p className="muted small">
            {t("holding.taxesLine", {
              taxes: money(row.taxes),
              realized: money(row.realized_taxes),
              income: money(row.income_taxes),
            })}
          </p>
        </section>
        <Income row={row} account={account} accounts={accounts} currency={currency} />
      </div>

      <Transactions
        row={row}
        asset={asset}
        account={account}
        accounts={accounts}
        search={search}
        here={here}
      />
    </>
  );
}

/** Dividends (or a bond's coupons) received, in money only (D-41). */
type IncomeProps = {
  row: HoldingRow;
  account: Account | null;
  accounts: Account[];
  currency: string; // the view's: one account's, or the reporting currency
};

function Income({ row, account, accounts, currency }: IncomeProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const bond = row.asset_type === "BOND";
  const type = bond ? "COUPON" : "DIVIDEND";
  const list = useTransactions({ account: account?.id, asset: row.asset_id, type, page: 0 });
  const money = (value: string) => formatMoney(value, currency, locale);
  const items = list.data?.items ?? [];
  const accountById = new Map(accounts.map((a) => [a.id, a]));

  return (
    <section className="card" aria-labelledby="holding-income">
      <h2 id="holding-income">{t(bond ? "holding.couponsHeading" : "holding.dividendsHeading")}</h2>
      <p className="muted small">{t("holding.incomeIntro")}</p>
      <dl className="income-figures">
        <div>
          <dt>{t("holding.gross")}</dt>
          <dd className="figure num">{money(row.income_gross)}</dd>
        </div>
        <div>
          <dt>{t("holding.withheld")}</dt>
          <dd className="figure num">{money(row.income_taxes)}</dd>
        </div>
        <div>
          <dt>{t("holding.net")}</dt>
          <dd className={`figure num ${tone(row.income_net)}`}>{money(row.income_net)}</dd>
        </div>
      </dl>
      {list.isError && <LoadError error={list.error} onRetry={() => void list.refetch()} />}
      {items.length > 0 && (
        <ul className="income-list">
          {items.map((tx) => (
            <li key={tx.id}>
              <span>
                <span className="strong block">{formatDay(tx.trade_date, locale)}</span>
                <span className="muted small">{t(`types.${tx.type}`)}</span>
              </span>
              {/* Each one in its account's currency (D-36), also over all accounts. */}
              <span className="num">
                {t("holding.netAmount", {
                  amount: formatMoney(
                    tx.total_amount ?? "0",
                    accountById.get(tx.account_id)?.currency ?? "",
                    locale,
                  ),
                })}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

type TransactionsProps = {
  row: HoldingRow;
  asset: Asset | undefined;
  account: Account | null;
  accounts: Account[];
  search: string;
  here: { backPath: string; back: string };
};

/** The holding's transactions, newest first, 50 per page (D-141), and, with one account in
 * view, "delete all" of them there (Q-12, D-146). */
function Transactions({ row, asset, account, accounts, search, here }: TransactionsProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const navigate = useNavigate();
  const client = useQueryClient();
  const warningTexts = useWarningTexts();
  const [page, setPage] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const list = useTransactions({ account: account?.id, asset: row.asset_id, page });
  const accountById = new Map(accounts.map((a) => [a.id, a]));
  const data = list.data;
  const total = data?.total ?? 0;
  const first = page * PAGE_SIZE + 1;
  const last = page * PAGE_SIZE + (data?.items.length ?? 0);
  const listSearch = `${search ? `${search}&` : "?"}asset=${row.asset_id}`;
  // All its transactions in the account; never another security's count, shown while this
  // one's is on its way (the page stays mounted from one detail to the next).
  const count = account && asset && !list.isPlaceholderData ? total : 0;

  return (
    <section className="card table-card" aria-labelledby="holding-transactions">
      <div className="selection-bar">
        <h2 id="holding-transactions">{t("holding.transactionsHeading")}</h2>
        <div className="actions">
          <Link to={{ pathname: "/transactions", search: listSearch }} className="button">
            {t("holding.editSeveral")}
          </Link>
          {count > 0 && (
            <button
              type="button"
              className="button button-danger-quiet"
              onClick={() => setDeleting(true)}
            >
              {t("transactions.deleteAll")}
            </button>
          )}
        </div>
      </div>
      {list.isPending && <Loading />}
      {list.isError && <LoadError error={list.error} onRetry={() => void list.refetch()} />}
      {data && (
        <div className="table-scroll">
          <table className="data" aria-busy={list.isFetching}>
            <caption className="visually-hidden">
              {t("transactions.caption", { first, last, total })}
            </caption>
            <thead>
              <tr>
                <th scope="col">{t("transactions.date")}</th>
                <th scope="col">{t("transactions.type")}</th>
                {!account && <th scope="col">{t("transactions.account")}</th>}
                <th scope="col" className="num">
                  {t("transactions.quantity")}
                </th>
                <th scope="col" className="num">
                  {t("holding.price")}
                </th>
                <th scope="col" className="num">
                  {t("transactions.amount")}
                </th>
                <th scope="col" className="num">
                  {t("holding.fees")}
                </th>
                <th scope="col" className="num">
                  {t("holding.taxes")}
                </th>
                <th scope="col" className="num">
                  {t("transactions.cashEffect")}
                </th>
                <th scope="col">
                  <span className="visually-hidden">{t("transactions.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((tx) => {
                const txAccount = accountById.get(tx.account_id);
                const cur = txAccount?.currency ?? "";
                const money = (value: string | null, signed = false) =>
                  value ? formatMoney(value, cur, locale, { signed }) : "—";
                const effect = cashEffect(tx.type, tx.total_amount);
                const what = {
                  type: t(`types.${tx.type}`),
                  asset: row.name,
                  date: formatDay(tx.trade_date, locale),
                };
                return (
                  <tr key={tx.id}>
                    <td className="nowrap">{formatDay(tx.trade_date, locale)}</td>
                    <td>
                      <span className="pill pill-soft">{t(`types.${tx.type}`)}</span>
                    </td>
                    {!account && (
                      <td>
                        <span className="chip">{txAccount?.name ?? "—"}</span>
                      </td>
                    )}
                    <td className="num">
                      {tx.quantity && (tx.type === "BUY" || tx.type === "SELL")
                        ? formatQuantity(tx.quantity, locale)
                        : "—"}
                    </td>
                    <td className="num nowrap">
                      {tx.unit_price ? quotePrice(t, row, tx.unit_price, locale) : "—"}
                    </td>
                    <td className="num nowrap">{money(tx.gross_amount ?? tx.total_amount)}</td>
                    <td className="num nowrap">{money(tx.fees)}</td>
                    <td className="num nowrap">{money(tx.taxes)}</td>
                    <td className="num nowrap">
                      {tx.affects_cash ? (effect ? money(effect, true) : "—") : t("transactions.noCash")}
                    </td>
                    <td>
                      <Link
                        to={{ pathname: `/transactions/${tx.id}/edit`, search }}
                        state={here}
                        aria-label={t("transactions.editRowAsset", what)}
                      >
                        {t("transactions.edit")}
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {total > PAGE_SIZE && (
        <div className="pager">
          <p className="muted small">{t("transactions.rows", { first, last, total })}</p>
          <div className="actions">
            <button type="button" className="button" disabled={page === 0} onClick={() => setPage(page - 1)}>
              {t("transactions.previous")}
            </button>
            <button
              type="button"
              className="button"
              disabled={last >= total}
              onClick={() => setPage(page + 1)}
            >
              {t("transactions.next")}
            </button>
          </div>
        </div>
      )}
      <p className="muted small table-note">{t("holding.noCashNote")}</p>
      {deleting && account && asset && (
        <DeleteAllDialog
          account={account}
          asset={asset}
          count={count}
          onClose={() => setDeleting(false)}
          onDeleted={({ out, count: deleted }) => {
            void afterChange(client);
            const text = [t("transactions.deleted", { count: deleted }), ...warningTexts(out.warnings)];
            navigate({ pathname: "/holdings", search }, { state: { notice: text.join(" ") } });
          }}
        />
      )}
    </section>
  );
}
