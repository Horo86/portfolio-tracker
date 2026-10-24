import type { TFunction } from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import { useAccountInView } from "../accounts";
import type { Account, AssetType, Holding, StaleFx } from "../api/types";
import { Field, SelectField } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { PriceNotes } from "../components/PriceStatus";
import { LoadError, Loading } from "../components/Status";
import { formatDay } from "../format/date";
import {
  formatMoney,
  formatPercent,
  formatPrice,
  formatQuantity,
  signOf,
} from "../format/decimal";
import { type HoldingRow, useHoldings } from "../holdings";
import { useMe } from "../session";

const ASSET_TYPES: readonly AssetType[] = ["ETF", "STOCK", "BOND"];
const TONE = { "-1": "negative", "0": "", "1": "positive" } as const;
const FILTERS = ["type", "currency", "closed"] as const;
type FilterName = (typeof FILTERS)[number];

/** Type, currency and "show closed positions" in the URL next to the account in view
 * (D-146); the search box only narrows the rows already loaded. */
function useFilters() {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries(
    FILTERS.map((name) => [name, params.get(name) ?? ""]),
  ) as Record<FilterName, string>;
  // At once, not in a transition: a controlled checkbox would otherwise flick back first.
  const update = (change: (next: URLSearchParams) => void) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        change(next);
        return next;
      },
      { flushSync: true },
    );
  return {
    values,
    set: (name: FilterName, value: string) =>
      update((next) => (value ? next.set(name, value) : next.delete(name))),
    clear: () => update((next) => FILTERS.forEach((name) => next.delete(name))),
  };
}

export function matches(row: Holding, filters: Record<FilterName, string>, text: string) {
  if (!row.open && filters.closed !== "1") return false;
  if (filters.type && row.asset_type !== filters.type) return false;
  if (filters.currency && row.quote_currency !== filters.currency) return false;
  const wanted = text.trim().toLocaleLowerCase();
  if (!wanted) return true;
  return [row.name, row.ticker, row.isin].some((v) => v?.toLocaleLowerCase().includes(wanted));
}

/** A price in the asset's quote unit: currency, its hundredths, or % of nominal (§2). */
export function quotePrice(t: TFunction, holding: Holding, value: string, locale: string) {
  if (holding.quote_unit === "PERCENT_OF_NOMINAL") {
    return t("summary.percent", { value: formatPrice(value, locale) });
  }
  if (holding.quote_unit === "MINOR_CURRENCY_UNIT") {
    return t("holdings.minorUnit", {
      value: formatPrice(value, locale),
      currency: holding.quote_currency,
    });
  }
  return formatPrice(value, locale, holding.quote_currency);
}

/** The average cost in the view's currency; a bond's as % of nominal (D-66). */
export function averageCost(t: TFunction, holding: Holding, currency: string, locale: string) {
  if (holding.pmc === null) return "—";
  if (holding.asset_type === "BOND") {
    return t("summary.percent", { value: formatPrice(holding.pmc, locale) });
  }
  return formatPrice(holding.pmc, locale, currency);
}

export function staleFxTexts(t: TFunction, notices: StaleFx[], locale: string): string[] {
  return notices.map((n) =>
    t("accounts.staleFx", {
      pair: `${n.from_currency}/${n.to_currency}`,
      date: formatDay(n.rate_date, locale),
    }),
  );
}

/** Mockup 02: the holdings of the account in view, or of all accounts in one row per
 * security (D-74, D-143). */
export default function Holdings() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const { account, accounts, search, pending } = useAccountInView();
  const holdings = useHoldings(account, !pending);
  const marketData = useMe().data?.market_data ?? "ON";
  const filters = useFilters();
  const [text, setText] = useState("");

  const view = holdings.data;
  const rows = view?.rows ?? [];
  const shown = rows.filter((row) => matches(row, filters.values, text));
  const open = rows.filter((row) => row.open).length;
  const currencies = [...new Set(rows.map((row) => row.quote_currency))].sort();
  const accountById = new Map(accounts.map((a) => [a.id, a]));
  const converted = !account && accounts.some((a) => view && a.currency !== view.currency);
  const filtered = text.trim() !== "" || FILTERS.some((name) => filters.values[name] !== "");

  return (
    <>
      <div className="page-head">
        <div>
          <PageTitle title={t("holdings.heading")} />
          {view && (
            <p className="muted">
              {account
                ? t("holdings.viewOne", { name: account.name, currency: view.currency, count: open })
                : t("holdings.viewAll", {
                    accounts: accounts.length,
                    currency: view.currency,
                    count: open,
                  })}
            </p>
          )}
        </div>
      </div>

      {view && (view.partial || view.stale_fx.length > 0) && (
        <ul className="warnings">
          {view.partial && <li>{t("accounts.partialNote")}</li>}
          {view.unavailable_currencies.length > 0 && (
            <li>
              {t("holdings.leftOut", {
                currencies: view.unavailable_currencies.join(", "),
                currency: view.currency,
              })}
            </li>
          )}
          {staleFxTexts(t, view.stale_fx, locale).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}

      {rows.length > 0 && (
        <section className="filters" aria-label={t("holdings.filters")}>
          <Field
            label={t("holdings.search")}
            type="search"
            value={text}
            placeholder={t("holdings.searchHint")}
            onChange={(e) => setText(e.target.value)}
          />
          <SelectField
            label={t("holdings.type")}
            value={filters.values.type}
            onChange={(e) => filters.set("type", e.target.value)}
          >
            <option value="">{t("holdings.allTypes")}</option>
            {ASSET_TYPES.map((type) => (
              <option key={type} value={type}>
                {t(`assetTypes.${type}`)}
              </option>
            ))}
          </SelectField>
          <SelectField
            label={t("holdings.currency")}
            value={filters.values.currency}
            onChange={(e) => filters.set("currency", e.target.value)}
          >
            <option value="">{t("holdings.allCurrencies")}</option>
            {currencies.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </SelectField>
          <label className="check filters-check">
            <input
              type="checkbox"
              checked={filters.values.closed === "1"}
              onChange={(e) => filters.set("closed", e.target.checked ? "1" : "")}
            />
            {t("holdings.showClosed")}
          </label>
          {filtered && (
            <button
              type="button"
              className="button filters-clear"
              onClick={() => {
                setText("");
                filters.clear();
              }}
            >
              {t("holdings.clearFilters")}
            </button>
          )}
        </section>
      )}

      {(pending || holdings.isPending) && <Loading />}
      {holdings.isError && (
        <LoadError error={holdings.error} onRetry={() => void holdings.refetch()} />
      )}
      {view && shown.length === 0 && (
        <section className="card empty-state">
          {accounts.length === 0 ? (
            <>
              <h2>{t("holdings.emptyHeading")}</h2>
              <p className="muted">{t("holdings.noAccounts")}</p>
              <Link to={{ pathname: "/accounts", search }} className="button button-primary">
                {t("transactions.toAccounts")}
              </Link>
            </>
          ) : rows.length === 0 || (open === 0 && !filtered) ? (
            <>
              <h2>{t("holdings.emptyHeading")}</h2>
              <p className="muted">{t("holdings.emptyText")}</p>
            </>
          ) : (
            <p className="muted">{t("holdings.noMatch")}</p>
          )}
        </section>
      )}
      {view && shown.length > 0 && (
        <section className="card table-card">
          <div className="table-scroll">
            <table className="data holdings" aria-busy={holdings.isFetching}>
              <caption className="visually-hidden">
                {t("holdings.caption", { count: shown.length })}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("holdings.asset")}</th>
                  {!account && <th scope="col">{t("holdings.account")}</th>}
                  <th scope="col" className="num">
                    {t("holdings.quantity")}
                  </th>
                  <th scope="col" className="num">
                    {t("holdings.pmc", { currency: view.currency })}
                  </th>
                  <th scope="col" className="num">
                    {t("holdings.lastPrice")}
                  </th>
                  <th scope="col" className="num">
                    {t("holdings.value", { currency: view.currency })}
                  </th>
                  <th scope="col" className="num">
                    {t("holdings.gainGross")}
                  </th>
                  <th scope="col" className="num">
                    {t("holdings.gainNet")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => (
                  <Row
                    key={row.asset_id}
                    row={row}
                    currency={view.currency}
                    accounts={account ? null : row.account_ids.map((id) => accountById.get(id))}
                    search={search}
                    marketData={marketData}
                  />
                ))}
              </tbody>
            </table>
          </div>
          {!account && (
            <p className="muted small table-note">
              {t("holdings.aggregatedNote")}
              {converted && ` ${t("holdings.convertedNote", { currency: view.currency })}`}
            </p>
          )}
        </section>
      )}
    </>
  );
}

type RowProps = {
  row: HoldingRow;
  currency: string;
  accounts: (Account | undefined)[] | null; // null: one account in view, no column
  search: string;
  marketData: "ON" | "OFF";
};

function Row({ row, currency, accounts, search, marketData }: RowProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const money = (value: string, signed = false) => formatMoney(value, currency, locale, { signed });
  const tone = (value: string | null) => (value === null ? "" : TONE[signOf(value)]);

  return (
    <tr>
      <td>
        <Link to={{ pathname: `/holdings/${row.asset_id}`, search }} className="strong">
          {row.name}
        </Link>
        <span className="muted small block">
          {t(`assetTypes.${row.asset_type}`)} · {row.quote_currency}
          {!row.open && ` · ${t("holdings.closed")}`}
        </span>
      </td>
      {accounts && (
        <td>
          <span className="chips">
            {accounts.map((a, i) => (
              <span key={a?.id ?? i} className="chip">
                {a?.name ?? "—"}
              </span>
            ))}
          </span>
        </td>
      )}
      <td className="num">{formatQuantity(row.quantity, locale)}</td>
      <td className="num nowrap">{averageCost(t, row, currency, locale)}</td>
      <td className="num">
        {row.price ? (
          <>
            <span className="nowrap">{quotePrice(t, row, row.price, locale)}</span>
            {row.price_date && (
              <span className="muted small block">{formatDay(row.price_date, locale)}</span>
            )}
          </>
        ) : (
          "—"
        )}
        <PriceNotes holding={row} marketData={marketData} />
      </td>
      <td className="num nowrap">
        {row.value === null ? "—" : money(row.value)}
        {row.partial && <span className="pill pill-warning block-pill">{t("accounts.partial")}</span>}
        {row.open && row.unrealized_pct !== null && (
          <span className={`small block ${tone(row.unrealized_pct)}`}>
            {t("holdings.unrealizedPct", {
              value: formatPercent(row.unrealized_pct, locale, { signed: true }),
            })}
          </span>
        )}
      </td>
      <td className={`num nowrap ${tone(row.gain_gross)}`}>
        {row.gain_gross === null ? "—" : money(row.gain_gross, true)}
      </td>
      <td className="num nowrap">
        {row.gain_net === null ? (
          "—"
        ) : (
          <span className={`gain-pill ${tone(row.gain_net)}`}>{money(row.gain_net, true)}</span>
        )}
      </td>
    </tr>
  );
}
