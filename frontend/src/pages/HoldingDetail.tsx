import { type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { useAccountInView } from "../accounts";
import type { Account, Asset } from "../api/types";
import { AssetEditDialog, DeleteAssetDialog } from "../components/AssetEditDialog";
import { GainsCard } from "../components/GainsCard";
import { Notice, useSavedNotice } from "../components/Notice";
import { PageTitle } from "../components/PageTitle";
import { StateCard } from "../components/StateCard";
import { PriceNotes } from "../components/PriceStatus";
import { LoadError, Loading } from "../components/Status";
import { formatDay } from "../format/date";
import {
  cashEffect,
  formatMoney,
  formatQuantity,
  signOf,
} from "../format/decimal";
import { exchangeName } from "../format/exchange";
import { type HoldingRow, type HoldingsView, mixedBondCost, useHoldings } from "../holdings";
import { asksForManualPrice, statusCode } from "../priceStatus";
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
  const { notice, announce } = useSavedNotice();

  const row = holdings.data?.rows.find((r) => r.asset_id === assetId);
  const asset = assets.data?.find((a) => a.id === assetId);
  const { edit, dialogs } = useAssetDialogs(asset, search, announce);
  const back = (
    <Link to={{ pathname: "/holdings", search }} className="back-link">
      {t("holding.back")}
    </Link>
  );

  let page: ReactNode;
  if (pending || holdings.isPending || assets.isPending) page = <Loading />;
  else if (holdings.isError) {
    page = <LoadError error={holdings.error} onRetry={() => void holdings.refetch()} level={1} />;
  } else if (!row && asset && !asset.in_use) {
    page = <Unused asset={asset} accounts={accounts} search={search} back={back} edit={edit} notice={notice} />;
  } else if (!row) {
    page = (
      <>
        {back}
        <PageTitle title={asset?.name ?? t("holding.notFoundHeading")} />
        {/* A change made from this page may have left the security out of the view. */}
        <Notice notice={notice} />
        {asset && account ? (
          <StateCard
            icon="holdings"
            actions={
              <Link to={`/holdings/${asset.id}`} className="button">
                {t("holding.toAllAccounts")}
              </Link>
            }
          >
            <p className="muted">{t("holding.notHere", { account: account.name })}</p>
          </StateCard>
        ) : asset && holdings.data.unavailable_currencies.length > 0 ? (
          <section className="card">
            <LeftOutOnly asset={asset} view={holdings.data} accounts={accounts} />
          </section>
        ) : (
          <StateCard icon={asset ? "holdings" : "notFound"}>
            <p className="muted">{t(asset ? "holding.noTransactions" : "holding.notFound")}</p>
          </StateCard>
        )}
      </>
    );
  } else {
    page = (
      <Detail
        row={row}
        asset={asset}
        currency={holdings.data.currency}
        leftOut={holdings.data.unavailable_currencies}
        account={account}
        accounts={accounts}
        search={search}
        back={back}
        edit={edit}
        notice={notice}
      />
    );
  }
  // The dialogs sit apart from the view: a refusal can change the view beneath them (a
  // security no transaction used becomes a holding) without closing them.
  return (
    <>
      {page}
      {dialogs}
    </>
  );
}

type Open = { kind: "edit" | "delete"; asset: Asset } | null;

/** "Modifica titolo" and, from its dialog, "Elimina titolo" for a security no transaction
 * uses (D-150); after a delete, the holdings say so. */
function useAssetDialogs(asset: Asset | undefined, search: string, announce: (text: string) => void) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const marketData = useMe().data?.market_data ?? "ON";
  // With the security as it was when it opened, should the list lose it meanwhile.
  const [open, setOpen] = useState<Open>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const refocus = useRef(false);

  // A dialog closed after its opener went away with the view: the focus goes to the same
  // button in the view shown now.
  useEffect(() => {
    if (!refocus.current || !buttonRef.current) return;
    refocus.current = false;
    if (opener.current && !opener.current.isConnected) buttonRef.current.focus();
  });

  const close = () => {
    refocus.current = true;
    setOpen(null);
  };
  const current = open && asset?.id === open.asset.id ? asset : open?.asset;

  const edit = asset && (
    <button
      type="button"
      className="button"
      ref={buttonRef}
      onClick={() => {
        opener.current = buttonRef.current;
        setOpen({ kind: "edit", asset });
      }}
    >
      {t("assetEdit.edit")}
    </button>
  );
  const dialogs = (
    <>
      {open?.kind === "edit" && current && (
        <AssetEditDialog
          asset={current}
          marketData={marketData}
          onClose={close}
          onSaved={() => {
            close();
            announce(t("assetEdit.saved"));
          }}
          onDelete={() => setOpen({ kind: "delete", asset: current })}
        />
      )}
      {open?.kind === "delete" && (
        <DeleteAssetDialog
          asset={open.asset}
          onClose={close}
          onDone={(text) => navigate({ pathname: "/holdings", search }, { state: { notice: text } })}
        />
      )}
    </>
  );
  return { edit, dialogs };
}

type UnusedProps = {
  asset: Asset;
  accounts: Account[];
  search: string;
  back: ReactNode;
  edit: ReactNode;
  notice: ReturnType<typeof useSavedNotice>["notice"];
};

/** A security no transaction uses (D-150): in no holdings row, so no figures; what can be
 * done with it, in every view. */
function Unused({ asset, accounts, search, back, edit, notice }: UnusedProps) {
  const { t } = useTranslation();
  const location = useLocation();
  return (
    <>
      {back}
      <div className="page-head">
        <div>
          <PageTitle title={asset.name} />
          <ul className="chips" aria-label={t("holding.facts")}>
            <li className="chip">{t(`assetTypes.${asset.asset_type}`)}</li>
            <li className="chip">{asset.quote_currency}</li>
            {asset.isin && <li className="chip">{asset.isin}</li>}
            {asset.ticker && <li className="chip">{asset.ticker}</li>}
          </ul>
        </div>
        <div className="actions head-actions">
          {accounts.length > 0 && (
            <Link
              to={{
                pathname: "/transactions/new",
                search: `${search ? `${search}&` : "?"}asset=${asset.id}`,
              }}
              state={{ backPath: location.pathname, back: search }}
              className="button button-primary"
            >
              {t("holding.addTransaction")}
            </Link>
          )}
          <Link to={{ pathname: `/holdings/${asset.id}/prices`, search }} className="button">
            {t("holding.manualPrices")}
          </Link>
          {edit}
        </div>
      </div>
      <Notice notice={notice} />
      <StateCard icon="holdings">
        <p className="muted">{t("holding.noTransactions")}</p>
      </StateCard>
    </>
  );
}

/** Over all accounts, a security held only in accounts left out for a missing rate (D-73)
 * has no row; its transactions say where it is. */
type LeftOutProps = { asset: Asset; view: HoldingsView; accounts: Account[] };

function LeftOutOnly({ asset, view, accounts }: LeftOutProps) {
  const { t } = useTranslation();
  const list = useTransactions({ asset: asset.id, page: 0 });
  if (list.isPending || list.isPlaceholderData) return <Loading />;
  if (list.isError) return <LoadError error={list.error} onRetry={() => void list.refetch()} />;
  if (list.data.total === 0) return <p className="muted">{t("holding.noTransactions")}</p>;
  const ids = new Set(list.data.items.map((tx) => tx.account_id));
  return (
    <>
      <p className="muted">
        {t("holding.leftOutOnly", {
          currencies: view.unavailable_currencies.join(", "),
          currency: view.currency,
        })}
      </p>
      <div className="actions">
        {accounts
          .filter((a) => ids.has(a.id))
          .map((a) => (
            <Link
              key={a.id}
              to={{ pathname: `/holdings/${asset.id}`, search: `?account=${a.id}` }}
              className="button"
            >
              {t("holding.openIn", { account: a.name })}
            </Link>
          ))}
      </div>
    </>
  );
}

type DetailProps = {
  row: HoldingRow;
  asset: Asset | undefined;
  currency: string;
  leftOut: string[]; // over all accounts: the currencies of the accounts left out (D-73)
  account: Account | null;
  accounts: Account[];
  search: string;
  back: ReactNode;
  edit: ReactNode; // "Modifica titolo", with its dialogs
  notice: ReturnType<typeof useSavedNotice>["notice"];
};

function Detail(props: DetailProps) {
  const { row, asset, currency, leftOut, account, accounts, search, back, edit, notice } = props;
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const location = useLocation();
  const marketData = useMe().data?.market_data ?? "ON";
  const money = (value: string, signed = false) => formatMoney(value, currency, locale, { signed });
  const accountById = new Map(accounts.map((a) => [a.id, a]));
  const bond = row.asset_type === "BOND";
  const here = { backPath: location.pathname, back: search };
  const pricesPath = `/holdings/${row.asset_id}/prices`;

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
            <Link to={{ pathname: pricesPath, search }} className="button">
              {t("holding.manualPrices")}
            </Link>
            {edit}
          </div>
        )}
      </div>

      <Notice notice={notice} />
      <PriceNotes holding={row} marketData={marketData} form="long" showOk />
      {asksForManualPrice(row, statusCode(row, marketData)) && (
        <p>
          <Link to={{ pathname: pricesPath, search }}>{t("priceStatus.enterByHand")}</Link>
        </p>
      )}
      {(row.partial || leftOut.length > 0 || row.stale_fx.length > 0) && (
        <ul className="warnings">
          {row.partial && <li>{t("accounts.partialNote")}</li>}
          {leftOut.length > 0 && (
            <li>{t("holdings.leftOut", { currencies: leftOut.join(", "), currency })}</li>
          )}
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
          {row.price_source === "PROVIDER" && row.price_exchange && row.price_date && (
            <p className="muted small">
              {t("holding.priceFrom", {
                exchange: exchangeName(t, row.price_exchange_code, row.price_exchange),
                date: formatDay(row.price_date, locale),
              })}
            </p>
          )}
        </section>
        <section className="card figure-card" aria-labelledby="holding-cost">
          {/* A partial row's value and gain cover the valued parts only (D-64): so does this. */}
          <h2 id="holding-cost" className="card-label">
            {t(row.partial ? "holding.costValued" : "holding.cost")}
          </h2>
          <p className="figure num">{money(row.partial ? row.valued_cost : row.cost_basis)}</p>
          {row.partial && (
            <p className="muted small">
              {t("holding.costTotal", { amount: money(row.cost_basis) })}
            </p>
          )}
          {row.pmc !== null && (
            <p className="muted small">
              {t("holding.pmcLine", { pmc: averageCost(t, row, currency, locale) })}
              {mixedBondCost(row, currency) && (
                <span className="block">
                  {t("holdings.pmcMixed", { currency: row.quote_currency })}
                </span>
              )}
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
        <GainsCard
          gains={row}
          currency={currency}
          incomeLabel={t(bond ? "holding.coupons" : "holding.dividends")}
          headingId="holding-gains"
        />
        <Income row={row} account={account} accounts={accounts} currency={currency} search={search} />
      </div>

      {/* A new account or security starts again from the first page. */}
      <Transactions
        key={`${account?.id ?? "all"} ${row.asset_id}`}
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
  search: string;
};

function Income({ row, account, accounts, currency, search }: IncomeProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const bond = row.asset_type === "BOND";
  const type = bond ? "COUPON" : "DIVIDEND";
  const list = useTransactions({ account: account?.id, asset: row.asset_id, type, page: 0 });
  const money = (value: string) => formatMoney(value, currency, locale);
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const accountById = new Map(accounts.map((a) => [a.id, a]));
  const allSearch = `${search ? `${search}&` : "?"}asset=${row.asset_id}&type=${type}`;

  return (
    <section className="card" aria-labelledby="holding-income">
      <h2 id="holding-income">{t(bond ? "holding.couponsHeading" : "holding.dividendsHeading")}</h2>
      <p className="muted small">{t("holding.incomeIntro")}</p>
      {/* Accrued interest paid or received counts in a bond's income (§6), so the total may
          differ from the coupons listed below. */}
      {bond && <p className="muted small">{t("holding.accruedNote")}</p>}
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
      {list.isError && <LoadError error={list.error} onRetry={() => void list.refetch()} level={3} />}
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
      {total > items.length && (
        <p className="muted small">
          {t("holding.incomeLatest", { shown: items.length, total })}{" "}
          <Link to={{ pathname: "/transactions", search: allSearch }}>{t("holding.incomeAll")}</Link>
        </p>
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
      {list.isError && <LoadError error={list.error} onRetry={() => void list.refetch()} level={3} />}
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
