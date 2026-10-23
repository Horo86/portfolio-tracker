import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router";

import { useAccountInView } from "../accounts";
import type {
  Account,
  Asset,
  ChangeOut,
  QuantityConflict,
  Transaction,
  TransactionType,
} from "../api/types";
import { Field, SelectField } from "../components/Field";
import { Notice, useNotice } from "../components/Notice";
import { PageTitle } from "../components/PageTitle";
import { LoadError, Loading } from "../components/Status";
import { formatDay } from "../format/date";
import { cashEffect, formatMoney, formatQuantity } from "../format/decimal";
import {
  afterChange,
  PAGE_SIZE,
  TRANSACTION_TYPES,
  useAssets,
  useTransactions,
} from "../transactions";
import { BulkDeleteDialog, BulkEditDialog, DeleteAllDialog } from "./TransactionBulk";
import { ConflictDialog, useWarningTexts } from "./TransactionDialogs";

const EXPORT_URL = "/api/v1/transactions/export.csv";
const FILTERS = ["type", "asset", "from", "to"] as const;
type FilterName = (typeof FILTERS)[number];

// Types whose quantity is not a holding's quantity show "—" (D-141).
const WITH_QUANTITY: ReadonlySet<TransactionType> = new Set(["BUY", "SELL"]);

/**
 * The filters (D-141), in the URL next to the account in view: type, asset, dates and the
 * page (from 1; absent on the first). Changing a filter goes back to the first page.
 */
function useFilters() {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries(
    FILTERS.map((name) => [name, params.get(name) ?? ""]),
  ) as Record<FilterName, string>;
  const pageParam = Number.parseInt(params.get("page") ?? "1", 10);
  const page = Number.isSafeInteger(pageParam) && pageParam > 1 ? pageParam - 1 : 0;

  const update = (change: (next: URLSearchParams) => void, replace = false) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        change(next);
        return next;
      },
      { replace },
    );
  return {
    values,
    page,
    active: FILTERS.some((name) => values[name] !== ""),
    set: (name: FilterName, value: string) =>
      update((next) => {
        if (value) next.set(name, value);
        else next.delete(name);
        next.delete("page");
      }),
    clear: () =>
      update((next) => {
        for (const name of FILTERS) next.delete(name);
        next.delete("page");
      }),
    setPage: (index: number, replace = false) =>
      update((next) => {
        if (index > 0) next.set("page", String(index + 1));
        else next.delete("page");
      }, replace),
  };
}

/** The confirmation a form left in the navigation state (`notice`), announced once: the
 * state is cleared, so a reload or the back button does not repeat it. */
function useSavedNotice() {
  const location = useLocation();
  const navigate = useNavigate();
  const { notice, announce } = useNotice();
  const text = (location.state as { notice?: unknown } | null)?.notice;
  useEffect(() => {
    if (typeof text !== "string") return;
    announce(text);
    const here = { pathname: location.pathname, search: location.search };
    navigate(here, { replace: true, state: null });
  }, [text, announce, navigate, location.pathname, location.search]);
  return { notice, announce };
}

const NONE: ReadonlySet<string> = new Set();

/**
 * The rows ticked on the page shown (D-141): the selection belongs to the address (account
 * in view, filters, page), so it is gone as soon as any of them changes.
 */
function useSelection(key: string) {
  const [selection, setSelection] = useState({ key, ids: NONE });
  const ids = selection.key === key ? selection.ids : NONE;
  const set = (next: ReadonlySet<string>) => setSelection({ key, ids: next });
  return {
    ids,
    toggle: (id: string) => {
      const next = new Set(ids);
      if (!next.delete(id)) next.add(id);
      set(next);
    },
    setAll: (all: readonly string[]) => set(new Set(all)),
    clear: () => set(NONE),
  };
}

type Bulk = "edit" | "delete" | "deleteAll";

/** Mockup 04: the transactions of the account in view, newest first, with filters. */
export default function Transactions() {
  const { t } = useTranslation();
  const location = useLocation();
  const { account, accounts, search, pending } = useAccountInView();
  const filters = useFilters();
  const { values, page } = filters;
  const assets = useAssets();
  const list = useTransactions(
    {
      account: account?.id,
      asset: values.asset,
      type: values.type,
      from: values.from,
      to: values.to,
      page,
    },
    !pending,
  );

  const assetById = new Map((assets.data ?? []).map((a) => [a.id, a]));
  const accountById = new Map(accounts.map((a) => [a.id, a]));
  const data = list.data;
  const total = data?.total ?? 0;
  const first = page * PAGE_SIZE + 1;
  const last = page * PAGE_SIZE + (data?.items.length ?? 0);
  const exportUrl = account ? `${EXPORT_URL}?account_id=${account.id}` : EXPORT_URL;
  const { notice, announce } = useSavedNotice();
  const client = useQueryClient();
  const warningTexts = useWarningTexts();
  const selection = useSelection(location.search);
  const [bulk, setBulk] = useState<Bulk | null>(null);
  const [conflict, setConflict] = useState<QuantityConflict | null>(null);

  const rows = data?.items ?? [];
  const selected = rows.filter((tx) => selection.ids.has(tx.id));
  // Delete all for a holding: one account in view and one security filtered (D-141); the
  // count is of all its transactions there, whatever the other filters.
  const holdingAsset = values.asset ? assetById.get(values.asset) : undefined;
  const holding = useTransactions(
    { account: account?.id, asset: values.asset, page: 0 },
    !pending && !!account && !!holdingAsset,
  );
  // Not the previous security's count, shown while this one's is on its way.
  const holdingCount =
    account && holdingAsset && !holding.isPlaceholderData ? (holding.data?.total ?? 0) : 0;

  const done = (key: string) => ({ out, count }: { out: ChangeOut; count: number }) => {
    setBulk(null);
    selection.clear();
    void afterChange(client);
    announce([t(key, { count }), ...warningTexts(out.warnings)].join(" "));
  };

  return (
    <>
      <div className="page-head">
        <div>
          <PageTitle title={t("transactions.heading")} />
          {data && (
            <p className="muted">
              {account
                ? t("transactions.viewOne", { name: account.name, count: total })
                : t("transactions.viewAll", { accounts: accounts.length, count: total })}
            </p>
          )}
        </div>
        <div className="actions head-actions">
          {accounts.length > 0 && (
            <Link to={{ pathname: "/transactions/new", search }} className="button button-primary">
              {t("transactions.add")}
            </Link>
          )}
          {accounts.length > 0 && (
            <Link to={{ pathname: "/import", search }} className="button">
              {t("transactions.importFromFile")}
            </Link>
          )}
          <a className="button" href={exportUrl} download>
            {t("transactions.export")}
          </a>
        </div>
      </div>

      <Notice notice={notice} />

      <section className="filters" aria-label={t("transactions.filters")}>
        <SelectField
          label={t("transactions.type")}
          value={values.type}
          onChange={(e) => filters.set("type", e.target.value)}
        >
          <option value="">{t("transactions.allTypes")}</option>
          {TRANSACTION_TYPES.map((type) => (
            <option key={type} value={type}>
              {t(`types.${type}`)}
            </option>
          ))}
        </SelectField>
        <SelectField
          label={t("transactions.asset")}
          value={values.asset}
          onChange={(e) => filters.set("asset", e.target.value)}
        >
          <option value="">{t("transactions.allAssets")}</option>
          {[...(assets.data ?? [])]
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.name}
              </option>
            ))}
        </SelectField>
        <Field
          label={t("transactions.from")}
          type="date"
          value={values.from}
          onChange={(e) => filters.set("from", e.target.value)}
        />
        <Field
          label={t("transactions.to")}
          type="date"
          value={values.to}
          onChange={(e) => filters.set("to", e.target.value)}
        />
        {filters.active && (
          <button type="button" className="button filters-clear" onClick={filters.clear}>
            {t("transactions.clearFilters")}
          </button>
        )}
      </section>

      {list.isPending && <Loading />}
      {list.isError && <LoadError error={list.error} onRetry={() => void list.refetch()} />}
      {data && data.items.length === 0 && (
        <section className="card empty-state">
          {page > 0 ? (
            <>
              <p className="muted">{t("transactions.pageEmpty")}</p>
              <button type="button" className="button" onClick={() => filters.setPage(0)}>
                {t("transactions.firstPage")}
              </button>
            </>
          ) : filters.active ? (
            <p className="muted">{t("transactions.noMatch")}</p>
          ) : (
            <>
              <h2>{t("transactions.emptyHeading")}</h2>
              <p className="muted">
                {accounts.length === 0 ? t("transactions.noAccounts") : t("transactions.emptyText")}
              </p>
              {accounts.length === 0 && (
                <Link to={{ pathname: "/accounts", search }} className="button button-primary">
                  {t("transactions.toAccounts")}
                </Link>
              )}
            </>
          )}
        </section>
      )}
      {data && data.items.length > 0 && (
        <section className="card table-card">
          <div className="selection-bar">
            <p className="muted small">{t("transactions.selected", { count: selected.length })}</p>
            <div className="actions">
              {holdingCount > 0 && (
                <button
                  type="button"
                  className="button button-danger-quiet"
                  onClick={() => setBulk("deleteAll")}
                >
                  {t("transactions.deleteAll")}
                </button>
              )}
              <button
                type="button"
                className="button"
                disabled={selected.length === 0}
                onClick={() => setBulk("edit")}
              >
                {t("transactions.editSelected")}
              </button>
              <button
                type="button"
                className="button"
                disabled={selected.length === 0}
                onClick={() => setBulk("delete")}
              >
                {t("transactions.deleteSelected")}
              </button>
            </div>
          </div>
          <div className="table-scroll">
            <table className="data" aria-busy={list.isFetching}>
              <caption className="visually-hidden">
                {t("transactions.caption", { first, last, total })}
              </caption>
              <thead>
                <tr>
                  <th scope="col" className="select-cell">
                    <SelectAll
                      count={selected.length}
                      total={rows.length}
                      onChange={(all) => selection.setAll(all ? rows.map((tx) => tx.id) : [])}
                    />
                  </th>
                  <th scope="col">{t("transactions.date")}</th>
                  <th scope="col">{t("transactions.type")}</th>
                  <th scope="col">{t("transactions.asset")}</th>
                  {!account && <th scope="col">{t("transactions.account")}</th>}
                  <th scope="col" className="num">
                    {t("transactions.quantity")}
                  </th>
                  <th scope="col" className="num">
                    {t("transactions.amount")}
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
                {data.items.map((tx) => (
                  <Row
                    key={tx.id}
                    tx={tx}
                    asset={tx.asset_id ? assetById.get(tx.asset_id) : undefined}
                    account={accountById.get(tx.account_id)}
                    showAccount={!account}
                    accountSearch={search}
                    listSearch={location.search}
                    selected={selection.ids.has(tx.id)}
                    onToggle={() => selection.toggle(tx.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <p className="muted small">{t("transactions.rows", { first, last, total })}</p>
            <div className="actions">
              <button
                type="button"
                className="button"
                disabled={page === 0}
                onClick={() => filters.setPage(page - 1)}
              >
                {t("transactions.previous")}
              </button>
              <button
                type="button"
                className="button"
                disabled={last >= total}
                onClick={() => filters.setPage(page + 1)}
              >
                {t("transactions.next")}
              </button>
            </div>
          </div>
        </section>
      )}

      {bulk === "edit" && (
        <BulkEditDialog
          txs={selected}
          accounts={accounts}
          onClose={() => setBulk(null)}
          onSaved={done("transactions.edited")}
          onConflict={setConflict}
        />
      )}
      {bulk === "delete" && (
        <BulkDeleteDialog
          ids={selected.map((tx) => tx.id)}
          onClose={() => setBulk(null)}
          onDeleted={done("transactions.deleted")}
          onConflict={setConflict}
        />
      )}
      {bulk === "deleteAll" && account && holdingAsset && (
        <DeleteAllDialog
          account={account}
          asset={holdingAsset}
          count={holdingCount}
          onClose={() => setBulk(null)}
          onDeleted={done("transactions.deleted")}
        />
      )}
      {conflict && (
        <ConflictDialog
          conflict={conflict}
          asset={assetById.get(conflict.asset_id)}
          onClose={() => setConflict(null)}
        />
      )}
    </>
  );
}

type SelectAllProps = {
  count: number;
  total: number;
  onChange: (all: boolean) => void;
};

/** Ticks every row of the page, or none; partly ticked when only some are. */
function SelectAll({ count, total, onChange }: SelectAllProps) {
  const { t } = useTranslation();
  return (
    <input
      type="checkbox"
      aria-label={t("transactions.selectPage")}
      checked={total > 0 && count === total}
      ref={(box) => {
        if (box) box.indeterminate = count > 0 && count < total;
      }}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

type RowProps = {
  tx: Transaction;
  asset: Asset | undefined;
  account: Account | undefined;
  showAccount: boolean;
  accountSearch: string; // the account in view, for the edit page
  listSearch: string; // the list as it is, to come back to
  selected: boolean;
  onToggle: () => void;
};

function Row(props: RowProps) {
  const { tx, asset, account, showAccount, accountSearch, listSearch, selected, onToggle } = props;
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const currency = account?.currency ?? "";
  const money = (value: string, signed = false) => formatMoney(value, currency, locale, { signed });

  let quantity = "—";
  if (tx.type === "STOCK_SPLIT" && tx.split_ratio) {
    // "2.5:1" is "2,5:1" in Italian.
    quantity = tx.split_ratio
      .split(":")
      .map((part) => formatQuantity(part, locale))
      .join(":");
  } else if (WITH_QUANTITY.has(tx.type) && tx.quantity) {
    quantity = formatQuantity(tx.quantity, locale);
  }
  const amount = tx.gross_amount ?? tx.total_amount;
  const effect = cashEffect(tx.type, tx.total_amount);
  const cash = effect && tx.affects_cash ? money(effect, true) : "—";

  const what = {
    type: t(`types.${tx.type}`),
    asset: asset?.name,
    date: formatDay(tx.trade_date, locale),
  };

  return (
    <tr>
      <td className="select-cell">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={t(asset ? "transactions.selectRowAsset" : "transactions.selectRow", what)}
        />
      </td>
      <td className="nowrap">{formatDay(tx.trade_date, locale)}</td>
      <td>
        <span className="pill pill-soft">{t(`types.${tx.type}`)}</span>
      </td>
      <td>{asset?.name ?? "—"}</td>
      {showAccount && (
        <td>
          <span className="chip">{account?.name ?? "—"}</span>
        </td>
      )}
      <td className="num">{quantity}</td>
      <td className="num nowrap">{amount ? money(amount) : "—"}</td>
      <td className="num nowrap">
        {cash}
        {!tx.affects_cash && (
          <span className="muted small block">{t("transactions.noCash")}</span>
        )}
      </td>
      <td>
        <Link
          to={{ pathname: `/transactions/${tx.id}/edit`, search: accountSearch }}
          state={{ back: listSearch }}
          aria-label={t(asset ? "transactions.editRowAsset" : "transactions.editRow", what)}
        >
          {t("transactions.edit")}
        </Link>
      </td>
    </tr>
  );
}
