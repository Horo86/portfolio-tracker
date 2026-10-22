import { useQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type {
  AssetType,
  ImportAssetPlan,
  ImportCategory,
  ImportLine,
  ImportPreview,
  QuoteUnit,
} from "../api/types";
import { currencyLabel } from "../components/CurrencySelect";
import { formatDay } from "../format/date";
import { cashEffect, formatMoney, formatQuantity } from "../format/decimal";
import { blockingText, lineText, noticeText, rowsText, type TextContext } from "../importText";
import { isTicked, type useImport } from "../imports";
import { PAGE_SIZE, useAssets } from "../transactions";

type Flow = ReturnType<typeof useImport>;

// The order of the tabs (D-142): what can be imported first, then what is not.
const CATEGORIES: readonly ImportCategory[] = [
  "TO_IMPORT",
  "TO_REVIEW",
  "SKIPPED_BY_CHOICE",
  "DUPLICATE",
  "NOT_RECOGNIZED",
  "AMBIGUOUS",
  "INVALID",
];
const SELECTABLE: ReadonlySet<ImportCategory> = new Set([
  "TO_IMPORT",
  "TO_REVIEW",
  "SKIPPED_BY_CHOICE",
]);
const ASSET_TYPES: readonly AssetType[] = ["ETF", "STOCK", "BOND"];

/** The texts' context for this preview: names of accounts and securities, file rows. */
export function useTextContext(preview: ImportPreview): (account: string | null) => TextContext {
  const { t, i18n } = useTranslation();
  return useMemo(() => {
    const assets = new Map(preview.assets.map((a) => [a.key, a.name]));
    const accounts = new Map(preview.accounts.map((a) => [a.key ?? "", a]));
    const rows = new Map(preview.lines.map((l) => [l.key, l.lines]));
    const base = {
      t,
      locale: i18n.language,
      assetName: (key: string) => assets.get(key) ?? key,
      accountName: (key: string) =>
        accounts.get(key)?.name ?? (key || preview.accounts[0]?.name) ?? "",
      rowsOf: (key: string) => rows.get(key) ?? [],
    };
    return (account) => ({
      ...base,
      currency: accounts.get(account ?? "")?.currency ?? preview.accounts[0]?.currency ?? "",
    });
  }, [preview, t, i18n.language]);
}

/** The big figures of mockup 08. */
export function Counts({ preview }: { preview: ImportPreview }) {
  const { t } = useTranslation();
  const c = preview.counts;
  const notices = preview.warnings.filter((w) => w.introduced).length + preview.notices.length;
  const figures = [
    ["accent", "selected", c.selected],
    ["", "duplicates", c.duplicates],
    ["negative", "notImported", c.not_recognized + c.ambiguous + c.invalid],
    ["warning", "warnings", notices],
  ] as const;
  return (
    <dl className="import-counts">
      {figures.map(([tone, name, value]) => (
        <div key={name} className={tone ? `import-count import-count-${tone}` : "import-count"}>
          <dt>{t(`importPreview.cards.${name}`)}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** What blocks the confirmation (D-113), red, with the texts that say how to solve it. */
export function Blocking({ preview }: { preview: ImportPreview }) {
  const { t } = useTranslation();
  const context = useTextContext(preview);
  const id = useId();
  if (preview.blocking.length === 0) return null;
  return (
    <section className="card import-blocking" aria-labelledby={id}>
      <h2 id={id}>{t("importPreview.blocking")}</h2>
      <ul>
        {preview.blocking.map((reason, index) => (
          <li key={index}>{blockingText(context(reason.params.account ?? null), reason)}</li>
        ))}
      </ul>
    </section>
  );
}

/** Notices and warnings, yellow: they do not block (D-142). MANUAL_OVERLAP comes first. */
export function Warnings({ preview }: { preview: ImportPreview }) {
  const { t, i18n } = useTranslation();
  const context = useTextContext(preview);
  const id = useId();
  const notices = [...preview.notices].sort(
    (a, b) => Number(b.code === "MANUAL_OVERLAP") - Number(a.code === "MANUAL_OVERLAP"),
  );
  const introduced = preview.warnings.filter((w) => w.introduced);
  const earlier = preview.warnings.length - introduced.length;
  if (notices.length === 0 && introduced.length === 0 && earlier === 0) return null;
  const rows = new Map(preview.lines.map((l) => [l.key, l.lines]));
  return (
    <section className="card import-warnings" aria-labelledby={id}>
      <h2 id={id}>{t("importPreview.warnings")}</h2>
      <p>{t("importPreview.warningsIntro")}</p>
      <ul>
        {notices.map((reason, index) => (
          <li key={`n${index}`} className={reason.code === "MANUAL_OVERLAP" ? "strong" : undefined}>
            {noticeText(context(reason.params.account ?? null), reason)}
          </li>
        ))}
        {introduced.map((w, index) => {
          const text = t(`warnings.${w.code}`, {
            account: context(w.account).accountName(w.account ?? ""),
            date: formatDay(w.date, i18n.language),
          });
          const where = w.line ? rows.get(w.line) : undefined;
          return (
            <li key={`w${index}`}>
              {text}
              {where && ` (${rowsText(t, where)})`}
            </li>
          );
        })}
      </ul>
      {earlier > 0 && (
        <p className="small">{t("importPreview.earlierWarnings", { count: earlier })}</p>
      )}
    </section>
  );
}

/** The new securities and what the user chooses for them (D-102, D-111, D-113). */
export function AssetChoices({ flow, preview }: { flow: Flow; preview: ImportPreview }) {
  const { t } = useTranslation();
  const id = useId();
  const plans = preview.assets.filter((a) => a.new || a.options.length > 0);
  const undecided = new Set(
    preview.blocking.filter((b) => b.code === "ASSET_CHOICE_REQUIRED").map((b) => b.params.asset),
  );
  if (plans.length === 0) return null;
  return (
    <section className="card" aria-labelledby={id}>
      <h2 id={id}>{t("importPreview.assets")}</h2>
      <ul className="import-assets">
        {plans.map((plan) => (
          <AssetChoice key={plan.key} plan={plan} flow={flow} undecided={undecided.has(plan.key)} />
        ))}
      </ul>
    </section>
  );
}

function unitsFor(type: AssetType | null): QuoteUnit[] {
  if (type === "BOND") return ["PERCENT_OF_NOMINAL"];
  return ["CURRENCY", "MINOR_CURRENCY_UNIT"];
}

type ChoiceProps = { plan: ImportAssetPlan; flow: Flow; undecided: boolean };

function AssetChoice({ plan, flow, undecided }: ChoiceProps) {
  const { t, i18n } = useTranslation();
  const currencies = useQuery({
    queryKey: ["currencies"],
    queryFn: ({ signal }) => api<string[]>("GET", "/currencies", undefined, signal),
    staleTime: Infinity,
  });
  const ids = { type: useId(), currency: useId(), unit: useId() };
  const choose = flow.chooseAsset;
  const currency = plan.quote_currency ?? "";
  // The units of the type, and the one the file gives even when it does not fit (ASSET_INVALID).
  const fileUnit = plan.quote_unit ? [plan.quote_unit] : [];
  const units = [...new Set([...unitsFor(plan.asset_type), ...fileUnit])];
  return (
    <li className="import-asset">
      <p className="import-asset-name">{plan.name}</p>
      <p className="muted small">
        {[plan.isin && `ISIN ${plan.isin}`, plan.ticker && `Ticker ${plan.ticker}`]
          .filter(Boolean)
          .join(" · ")}
      </p>
      <div className="form-grid form-grid-3">
        {plan.options.length > 0 && (
          <ExistingChoice plan={plan} undecided={undecided} onChoose={choose} />
        )}
        {plan.new && !undecided && (
          <>
            <div className="field">
              <label htmlFor={ids.type}>{t("importPreview.assetType")}</label>
              <select
                id={ids.type}
                value={plan.asset_type ?? ""}
                aria-invalid={plan.asset_type === null ? true : undefined}
                onChange={(e) =>
                  // The unit follows the type again (the server's proposal, D-102).
                  choose(plan.key, { asset_type: e.target.value as AssetType, quote_unit: null })
                }
              >
                {plan.asset_type === null && <option value="">{t("importPreview.choose")}</option>}
                {ASSET_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {t(`assetTypes.${type}`)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor={ids.currency}>{t("importPreview.quoteCurrency")}</label>
              <select
                id={ids.currency}
                value={currency}
                aria-invalid={currency === "" ? true : undefined}
                onChange={(e) => choose(plan.key, { quote_currency: e.target.value })}
              >
                {currency === "" && <option value="">{t("importPreview.choose")}</option>}
                {(currencies.data ?? (currency ? [currency] : [])).map((code) => (
                  <option key={code} value={code}>
                    {currencyLabel(code, i18n.language)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor={ids.unit}>{t("importPreview.quoteUnit")}</label>
              <select
                id={ids.unit}
                value={plan.quote_unit ?? ""}
                disabled={plan.asset_type === null}
                onChange={(e) => choose(plan.key, { quote_unit: e.target.value as QuoteUnit })}
              >
                {plan.quote_unit === null && <option value="">{t("importPreview.choose")}</option>}
                {units.map((unit) => (
                  <option key={unit} value={unit}>
                    {t(`quoteUnits.${unit}`, { currency: currency || "—" })}
                  </option>
                ))}
              </select>
            </div>
          </>
        )}
      </div>
      {plan.new && (
        <p className="muted small">
          {t(plan.isin ? "importPreview.pricesLater" : "importPreview.manualPrices")}
        </p>
      )}
    </li>
  );
}

type ExistingProps = {
  plan: ImportAssetPlan;
  undecided: boolean;
  onChoose: Flow["chooseAsset"];
};

/** A CSV security without ISIN whose ticker some of the user's securities have (D-113). */
function ExistingChoice({ plan, undecided, onChoose }: ExistingProps) {
  const { t } = useTranslation();
  const assets = useAssets();
  const id = useId();
  const named = new Map((assets.data ?? []).map((a) => [a.id, a.name]));
  return (
    <div className="field">
      <label htmlFor={id}>{t("importPreview.asset")}</label>
      <select
        id={id}
        value={undecided ? "" : (plan.asset_id ?? "new")}
        aria-invalid={undecided ? true : undefined}
        onChange={(e) =>
          onChoose(plan.key, { asset_id: e.target.value === "new" ? null : e.target.value })
        }
      >
        {undecided && <option value="">{t("importPreview.choose")}</option>}
        {plan.options.map((option) => (
          <option key={option} value={option}>
            {named.get(option) ?? option}
          </option>
        ))}
        <option value="new">{t("importPreview.newAsset")}</option>
      </select>
    </div>
  );
}

/** The lines of the file by category, one tab each (D-142), 50 per page. */
export function Lines({ flow, preview }: { flow: Flow; preview: ImportPreview }) {
  const { t } = useTranslation();
  const present = CATEGORIES.filter((c) => preview.lines.some((l) => l.category === c));
  const [chosen, setChosen] = useState<ImportCategory | null>(null);
  const [page, setPage] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const baseId = useId();
  const active = chosen && present.includes(chosen) ? chosen : (present[0] ?? null);
  if (active === null) return null;

  const show = (category: ImportCategory) => {
    setChosen(category);
    setPage(0);
  };
  const keys = (event: KeyboardEvent, index: number) => {
    const last = present.length - 1;
    const next =
      event.key === "ArrowRight" ? (index === last ? 0 : index + 1)
      : event.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : null;
    if (next === null) return;
    event.preventDefault();
    show(present[next]);
    tabs.current[next]?.focus();
  };

  const lines = preview.lines.filter((l) => l.category === active);
  const pages = Math.max(1, Math.ceil(lines.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const shown = lines.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE);
  const first = current * PAGE_SIZE + 1;
  const last = current * PAGE_SIZE + shown.length;

  return (
    <section className="card table-card" aria-label={t("importPreview.linesLabel")}>
      <div role="tablist" className="tabs" aria-label={t("importPreview.tabs")}>
        {present.map((category, index) => (
          <button
            key={category}
            ref={(element) => {
              tabs.current[index] = element;
            }}
            type="button"
            role="tab"
            id={`${baseId}-tab-${category}`}
            aria-selected={category === active}
            aria-controls={`${baseId}-panel`}
            tabIndex={category === active ? 0 : -1}
            className="tab"
            onClick={() => show(category)}
            onKeyDown={(event) => keys(event, index)}
          >
            {t("importPreview.tab", {
              label: t(`importCategories.${category}`),
              count: preview.lines.filter((l) => l.category === category).length,
            })}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${baseId}-panel`} aria-labelledby={`${baseId}-tab-${active}`}>
        {SELECTABLE.has(active) && <TabSelection flow={flow} lines={lines} />}
        <div className="table-scroll">
          <table className="data" aria-busy={flow.pending}>
            <caption className="visually-hidden">
              {t("importPreview.caption", {
                category: t(`importCategories.${active}`),
                first,
                last,
                total: lines.length,
              })}
            </caption>
            <thead>
              <tr>
                {SELECTABLE.has(active) && (
                  <th scope="col" className="select-cell">
                    <span className="visually-hidden">{t("importPreview.columns.select")}</span>
                  </th>
                )}
                <th scope="col">{t("importPreview.columns.date")}</th>
                <th scope="col">{t("importPreview.columns.type")}</th>
                <th scope="col">{t("importPreview.columns.asset")}</th>
                <th scope="col" className="num">
                  {t("importPreview.columns.quantity")}
                </th>
                <th scope="col" className="num">
                  {t("importPreview.columns.amount")}
                </th>
                <th scope="col" className="num">
                  {t("importPreview.columns.cash")}
                </th>
                <th scope="col">{t("importPreview.columns.notes")}</th>
                <th scope="col">{t("importPreview.columns.rows")}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((line) => (
                <Row
                  key={line.key}
                  line={line}
                  preview={preview}
                  selectable={SELECTABLE.has(active)}
                  flow={flow}
                />
              ))}
            </tbody>
          </table>
        </div>
        {lines.length > PAGE_SIZE && (
          <div className="pager">
            <p className="muted small">
              {t("transactions.rows", { first, last, total: lines.length })}
            </p>
            <div className="actions">
              <button
                type="button"
                className="button"
                disabled={current === 0}
                onClick={() => setPage(current - 1)}
              >
                {t("transactions.previous")}
              </button>
              <button
                type="button"
                className="button"
                disabled={current >= pages - 1}
                onClick={() => setPage(current + 1)}
              >
                {t("transactions.next")}
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

/** Ticks a whole tab, or its account expenses only (import/directa.md §7). */
function TabSelection({ flow, lines }: { flow: Flow; lines: ImportLine[] }) {
  const { t } = useTranslation();
  const keys = lines.map((l) => l.key);
  const expenses = lines.filter((l) => l.account_expense);
  const expensesTicked = expenses.filter((l) => isTicked(l, flow.ticks)).length;
  const id = useId();
  return (
    <div className="selection-bar">
      <div className="actions">
        <button type="button" className="button" onClick={() => flow.tick(keys, true)}>
          {t("importPreview.selectAll", { count: lines.length })}
        </button>
        <button type="button" className="button" onClick={() => flow.tick(keys, false)}>
          {t("importPreview.selectNone")}
        </button>
      </div>
      {expenses.length > 0 && (
        <div className="check">
          <input
            id={id}
            type="checkbox"
            checked={expensesTicked === expenses.length}
            ref={(box) => {
              if (box) box.indeterminate = expensesTicked > 0 && expensesTicked < expenses.length;
            }}
            onChange={(e) => flow.tick(expenses.map((l) => l.key), e.target.checked)}
          />
          <label htmlFor={id}>{t("importPreview.expenses", { count: expenses.length })}</label>
        </div>
      )}
    </div>
  );
}

type RowProps = { line: ImportLine; preview: ImportPreview; selectable: boolean; flow: Flow };

function Row({ line, preview, selectable, flow }: RowProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const context = useTextContext(preview)(line.account);
  const asset = line.asset ? context.assetName(line.asset) : (line.original_name ?? null);
  const amount = line.gross_amount ?? line.total_amount;
  const cash = line.type ? cashEffect(line.type, line.total_amount) : null;
  const notes = [...line.reasons, ...line.info].map((r) => lineText(context, r));
  const what = [
    line.type ? t(`types.${line.type}`) : null,
    asset,
    line.trade_date ? formatDay(line.trade_date, locale) : rowsText(t, line.lines),
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <tr>
      {selectable && (
        <td className="select-cell">
          <input
            type="checkbox"
            checked={isTicked(line, flow.ticks)}
            onChange={(e) => flow.tick([line.key], e.target.checked)}
            aria-label={t("importPreview.selectRow", { what })}
          />
        </td>
      )}
      <td className="nowrap">{line.trade_date ? formatDay(line.trade_date, locale) : "—"}</td>
      <td>{line.type ? <span className="pill pill-soft">{t(`types.${line.type}`)}</span> : "—"}</td>
      <td>{asset ?? "—"}</td>
      <td className="num">{line.quantity ? formatQuantity(line.quantity, locale) : "—"}</td>
      <td className="num nowrap">{amount ? formatMoney(amount, context.currency, locale) : "—"}</td>
      <td className="num nowrap">
        {cash && line.affects_cash
          ? formatMoney(cash, context.currency, locale, { signed: true })
          : "—"}
        {cash && !line.affects_cash && (
          <span className="muted small block">{t("importPreview.noCash")}</span>
        )}
      </td>
      <td>
        {notes.length > 0 ? (
          <ul className="plain-list">
            {notes.map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        ) : (
          "—"
        )}
      </td>
      <td className="nowrap">{rowsText(t, line.lines)}</td>
    </tr>
  );
}
