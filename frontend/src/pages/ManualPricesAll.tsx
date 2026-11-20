import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useAccountInView } from "../accounts";
import { api, ApiError } from "../api/client";
import type { FieldIssue, ManualPrice } from "../api/types";
import { DateField } from "../components/DateField";
import { DecimalField } from "../components/DecimalField";
import { PageTitle } from "../components/PageTitle";
import { StateCard } from "../components/StateCard";
import { LoadError, Loading } from "../components/Status";
import { Body, Head, Table, Td, Th, Tr } from "../components/Table";
import { errorText, issueText } from "../errors";
import { formatDay, today } from "../format/date";
import { MAX_AMOUNT_DECIMALS, parseDecimalInput } from "../format/decimalInput";
import { type HoldingRow, useHoldings } from "../holdings";
import { afterPriceChange, dayProblem } from "../manualPrices";
import { needsManualPrice } from "../priceStatus";
import { useMe } from "../session";
import { quotePrice } from "./Holdings";

// The quote unit decides the wording, as in the single form (manualPrice.*): a bond's price
// is a % of nominal, clean price.
const UNIT_LABEL = {
  CURRENCY: "manualPrice.price",
  MINOR_CURRENCY_UNIT: "manualPrice.priceMinor",
  PERCENT_OF_NOMINAL: "manualPricesAll.priceBond",
} as const;

const without = <T,>(all: Record<string, T>, id: string) =>
  Object.fromEntries(Object.entries(all).filter(([key]) => key !== id));

/**
 * One page for the manual prices of every open holding whose prices no source will ever fill
 * (`needsManualPrice`: no engine, no ISIN, or market data off). Wider than the holdings filter
 * `source=none`, which stays on `NOT_AVAILABLE` only (D-175 (1)). No mockup: built from the pattern of the
 * single form (screen 12, D-149) and the list pages. One date for all rows; each typed price
 * goes to `PUT /assets/{id}/prices`, one by one, each with its own result.
 */
export default function ManualPricesAll() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const client = useQueryClient();
  const { account, search, pending } = useAccountInView();
  const holdings = useHoldings(account, !pending);
  const marketData = useMe().data?.market_data ?? "ON";

  const [day, setDay] = useState(today);
  const [values, setValues] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const working = useRef(false); // set before any render: a second click sees it
  const dayRef = useRef<HTMLInputElement>(null);
  const fields = useRef(new Map<string, HTMLInputElement | null>());

  const rows = (holdings.data?.rows ?? []).filter((row) => row.open && needsManualPrice(row, marketData));
  const typed = rows.filter((row) => (values[row.asset_id] ?? "").trim() !== "");
  const dayIssue = dayProblem(day, today());

  const back = (
    <Link to={{ pathname: "/holdings", search }} className="back-link">
      {t("manualPricesAll.back")}
    </Link>
  );

  const parse = (row: HoldingRow) =>
    parseDecimalInput(values[row.asset_id] ?? "", locale, { maxDecimals: MAX_AMOUNT_DECIMALS });

  /** The text a typed price is refused with, before any request. */
  const problem = (row: HoldingRow): string | undefined => {
    const parsed = parse(row);
    if (parsed.kind !== "error") return undefined;
    return parsed.problem === "negative"
      ? t("manualPrice.negative")
      : t(`input.${parsed.problem}`, { max: MAX_AMOUNT_DECIMALS });
  };

  const change = (row: HoldingRow, value: string) => {
    if (busy) return;
    setValues((all) => ({ ...all, [row.asset_id]: value }));
    setErrors((all) => without(all, row.asset_id));
    setSaved((all) => without(all, row.asset_id));
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || working.current) return;
    setSubmitted(true);
    if (dayIssue) {
      dayRef.current?.focus();
      return;
    }
    const bad = typed.find((row) => problem(row));
    if (bad) {
      fields.current.get(bad.asset_id)?.focus();
      return;
    }
    working.current = true;
    setBusy(true);
    const failed: string[] = [];
    const nextErrors: Record<string, string> = {};
    const nextSaved: Record<string, boolean> = {};
    const cleared: string[] = [];
    for (const row of typed) {
      const parsed = parse(row);
      if (parsed.kind !== "ok") continue;
      try {
        await api<ManualPrice>("PUT", `/assets/${row.asset_id}/prices`, { day, close: parsed.value });
        nextSaved[row.asset_id] = true;
        cleared.push(row.asset_id);
        void afterPriceChange(client, row.asset_id);
      } catch (error) {
        failed.push(row.asset_id);
        const issues =
          error instanceof ApiError && error.code === "VALIDATION_FAILED"
            ? ((error.extra.issues as FieldIssue[] | undefined) ?? [])
            : [];
        nextErrors[row.asset_id] = issues[0] ? issueText(t, issues[0].code) : errorText(t, error);
      }
    }
    // Saved rows clear their field; a failed row keeps its value and shows why.
    setValues((all) => Object.fromEntries(Object.entries(all).filter(([id]) => !cleared.includes(id))));
    setErrors(nextErrors);
    setSaved(nextSaved);
    setBusy(false);
    working.current = false;
    setSubmitted(false);
    if (failed[0]) fields.current.get(failed[0])?.focus();
  }

  if (pending || holdings.isPending) return <Loading />;
  if (holdings.isError) {
    return <LoadError error={holdings.error} onRetry={() => void holdings.refetch()} level={1} />;
  }

  // After a save the list may be empty of typed rows but still show the saved ones: rows stay
  // listed (their price is now manual), so the empty state is only for "no holding at all".
  if (rows.length === 0) {
    return (
      <>
        {back}
        <PageTitle title={t("manualPricesAll.title")} />
        <StateCard icon="holdings" heading={t("manualPricesAll.empty")}>
          <p className="muted">{t("manualPricesAll.emptyText")}</p>
        </StateCard>
      </>
    );
  }

  const dayError =
    submitted && dayIssue === "required"
      ? t("validation.required")
      : dayIssue === "future" && submitted
        ? issueText(t, "future_date")
        : undefined;

  return (
    <>
      {back}
      <div className="page-head">
        <div>
          <PageTitle title={t("manualPricesAll.title")} />
          <p className="muted">{t("manualPricesAll.intro")}</p>
        </div>
      </div>

      <form onSubmit={(event) => void submit(event)} noValidate>
        <section className="card manual-prices-all">
          <DateField
            label={t("manualPricesAll.day")}
            value={day}
            max={today()}
            busy={busy}
            onChange={(value) => {
              if (!busy) setDay(value);
            }}
            error={dayError}
            inputRef={dayRef}
          />
          <div className="table-scroll">
            <Table cards className="data manual-prices-all-table">
              <caption className="visually-hidden">{t("manualPricesAll.tableCaption")}</caption>
              <Head>
                <Tr>
                  <Th scope="col">{t("manualPricesAll.security")}</Th>
                  <Th scope="col">{t("manualPricesAll.lastPrice")}</Th>
                  <Th scope="col">{t("manualPricesAll.newPrice")}</Th>
                </Tr>
              </Head>
              <Body>
                {rows.map((row) => {
                  const unit = t(UNIT_LABEL[row.quote_unit], { currency: row.quote_currency });
                  const error = errors[row.asset_id] ?? problem(row);
                  return (
                    <Tr key={row.asset_id}>
                      <Td wide className="manual-all-name">
                        <Link to={{ pathname: `/holdings/${row.asset_id}`, search }}>{row.name}</Link>
                      </Td>
                      <Td wide className="nowrap" label={t("manualPricesAll.lastPrice")}>
                        {row.price && row.price_date && row.price_source === "MANUAL"
                          ? `${quotePrice(t, row, row.price, locale)} · ${formatDay(row.price_date, locale)}`
                          : t("manualPricesAll.noPrice")}
                      </Td>
                      <Td wide className="manual-all-field">
                        <DecimalField
                          label={`${row.name}: ${unit}`}
                          hint={unit}
                          value={values[row.asset_id] ?? ""}
                          onChange={(value) => change(row, value)}
                          onBlur={() => {}}
                          error={error}
                          inputRef={(node) => {
                            fields.current.set(row.asset_id, node);
                          }}
                        />
                        <div role="status" className={saved[row.asset_id] ? "notice" : undefined}>
                          {saved[row.asset_id] ? t("manualPricesAll.saved") : null}
                        </div>
                      </Td>
                    </Tr>
                  );
                })}
              </Body>
            </Table>
          </div>
          <div className="actions">
            <button type="submit" className="button button-primary" disabled={busy || typed.length === 0}>
              {t("manualPricesAll.save")}
            </button>
          </div>
        </section>
      </form>
    </>
  );
}
