import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { useAccountInView } from "../accounts";
import { api, ApiError } from "../api/client";
import type { Asset, FieldIssue, ManualPrice } from "../api/types";
import { DecimalField } from "../components/DecimalField";
import { Dialog } from "../components/Dialog";
import { Field } from "../components/Field";
import { FormError } from "../components/FormError";
import { Notice, useNotice } from "../components/Notice";
import { PageTitle } from "../components/PageTitle";
import { PriceNotes } from "../components/PriceStatus";
import { LoadError, Loading } from "../components/Status";
import { issueText } from "../errors";
import { formatDay, today } from "../format/date";
import { decimalToInput, MAX_AMOUNT_DECIMALS, parseDecimalInput } from "../format/decimalInput";
import { useHoldings } from "../holdings";
import { afterPriceChange, dayProblem, SHOWN_PRICES, useManualPrices } from "../manualPrices";
import { useMe } from "../session";
import { useAssets } from "../transactions";
import { quotePrice } from "./Holdings";

/**
 * Mockup 12, as D-149 and the appendix of the ui README amend it: the user's own manual prices
 * of one security, newest first, and the form that sets the price of a day. Provider closes are
 * never listed (D-116). The banner says what the security is valued at now.
 */
export default function ManualPrices() {
  const { t } = useTranslation();
  const { assetId = "" } = useParams();
  const { search } = useAccountInView();
  const assets = useAssets();
  const asset = assets.data?.find((a) => a.id === assetId);

  if (assets.isPending) return <Loading />;
  if (assets.isError) return <LoadError error={assets.error} onRetry={() => void assets.refetch()} />;
  if (!asset) {
    return (
      <>
        <Link to={{ pathname: "/holdings", search }} className="back-link">
          {t("holding.back")}
        </Link>
        <PageTitle title={t("holding.notFoundHeading")} />
        <section className="card empty-state">
          <p className="muted">{t("holding.notFound")}</p>
        </section>
      </>
    );
  }
  return <Prices key={asset.id} asset={asset} search={search} />;
}

type PricesProps = { asset: Asset; search: string };

const UNIT_LABEL = {
  CURRENCY: "manualPrice.price",
  MINOR_CURRENCY_UNIT: "manualPrice.priceMinor",
  PERCENT_OF_NOMINAL: "manualPrice.priceBond",
} as const;

function Prices({ asset, search }: PricesProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const client = useQueryClient();
  const { account, pending } = useAccountInView();
  const holdings = useHoldings(account, !pending);
  const inView = holdings.data?.rows.find((r) => r.asset_id === asset.id);
  // The price status and the price in use do not depend on the account: when the account in
  // view does not hold the security, they come from all accounts.
  const all = useHoldings(null, account !== null && holdings.isSuccess && !inView);
  const row = inView ?? all.data?.rows.find((r) => r.asset_id === asset.id);
  const marketData = useMe().data?.market_data ?? "ON";
  const prices = useManualPrices(asset.id);
  const { notice, announce } = useNotice();
  const formHeading = useId();
  const listHeading = useId();

  const [editing, setEditing] = useState<ManualPrice | null>(null);
  const [day, setDay] = useState(today);
  const [close, setClose] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [deleting, setDeleting] = useState<ManualPrice | null>(null);
  const dayRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef<HTMLInputElement>(null);
  const noticeRef = useRef<HTMLDivElement>(null);
  const tableRef = useRef<HTMLTableElement>(null);
  const deletedId = useRef<string | null>(null);

  const list = prices.data ?? [];
  const shown = showAll ? list : list.slice(0, SHOWN_PRICES);
  const date = (value: string) => formatDay(value, locale);

  // Set when a save starts, before any render: a second click in the same moment sees it,
  // where the mutation's own state may not have reached this closure yet.
  const saving = useRef(false);
  const save = useMutation({
    onSettled: () => {
      saving.current = false;
    },
    mutationFn: (body: { day: string; close: string }) =>
      api<ManualPrice>("PUT", `/assets/${asset.id}/prices`, body),
    onSuccess: (saved) => {
      void afterPriceChange(client, asset.id);
      announce(t("manualPrice.saved", { date: date(saved.day) }));
      startNew();
    },
  });

  // Editing: the price field takes the focus, the day stays as it is (D-149).
  useEffect(() => {
    if (editing) closeRef.current?.focus();
  }, [editing]);

  // After a deletion the dialog and the row are gone: the focus goes to the confirmation.
  useEffect(() => {
    const id = deletedId.current;
    if (id && prices.data && !prices.data.some((price) => price.id === id)) {
      deletedId.current = null;
      noticeRef.current?.focus();
    }
  }, [prices.data]);

  // "Show all" goes away with the click: the focus goes to the list it lengthened.
  useEffect(() => {
    if (showAll) tableRef.current?.focus();
  }, [showAll]);

  function startNew() {
    setEditing(null);
    setDay(today());
    setClose("");
    setSubmitted(false);
    save.reset();
  }

  const edit = (price: ManualPrice) => {
    if (save.isPending) return;
    setEditing(price);
    setDay(price.day);
    setClose(decimalToInput(price.close, locale));
    setSubmitted(false);
    save.reset();
  };

  // While saving the fields are read-only: what is saved is what was there at the click.
  const change = (set: (value: string) => void) => (value: string) => {
    if (save.isPending) return;
    set(value);
    save.reset();
  };

  // An edited price keeps its day (D-149), whatever the field receives; the server checks it,
  // and it may be tomorrow, which the server allows.
  const priceDay = editing?.day ?? day;
  const parsed = parseDecimalInput(close, locale, { maxDecimals: MAX_AMOUNT_DECIMALS });
  const dayIssue = editing ? null : dayProblem(day, today());
  // The server's refusal of a field is shown at the field; anything else above the form.
  const failed = save.error;
  const issues =
    failed instanceof ApiError && failed.code === "VALIDATION_FAILED"
      ? ((failed.extra.issues as FieldIssue[] | undefined) ?? [])
      : [];
  const dayRefused = issues.find((issue) => issue.field === "day");
  const closeRefused = issues.find((issue) => issue.field === "close");

  let dayError: string | undefined;
  if (submitted && dayIssue === "required") dayError = t("validation.required");
  else if (submitted && dayIssue === "future") dayError = issueText(t, "future_date");
  else if (dayRefused) dayError = issueText(t, dayRefused.code);

  let closeError: string | undefined;
  if (parsed.kind === "error") {
    closeError = t(`input.${parsed.problem}`, { max: MAX_AMOUNT_DECIMALS });
  } else if (submitted && parsed.kind === "empty") closeError = t("validation.required");
  else if (closeRefused) closeError = issueText(t, closeRefused.code);

  const replaced = !editing && list.some((price) => price.day === day);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (save.isPending || saving.current) return;
    setSubmitted(true);
    if (dayIssue) dayRef.current?.focus();
    else if (parsed.kind !== "ok") closeRef.current?.focus();
    else {
      saving.current = true;
      save.mutate({ day: priceDay, close: parsed.value });
    }
  };

  const unitLabel = t(UNIT_LABEL[asset.quote_unit], { currency: asset.quote_currency });

  return (
    <>
      <Link to={{ pathname: `/holdings/${asset.id}`, search }} className="back-link">
        {`← ${asset.name}`}
      </Link>
      <div className="page-head">
        <div>
          <PageTitle title={t("manualPrice.title")} />
          <p className="muted">
            {[asset.name, t(`assetTypes.${asset.asset_type}`), asset.quote_currency].join(" · ")}
          </p>
        </div>
      </div>

      <Notice notice={notice} ref={noticeRef} />
      {row && <PriceNotes holding={row} marketData={marketData} form="long" showOk />}
      {row?.price && row.price_date && row.price_source && (
        <p className="muted">
          {t("manualPrice.inUse", {
            price: quotePrice(t, row, row.price, locale),
            date: date(row.price_date),
            source: t(row.price_source === "MANUAL" ? "manualPrice.sourceManual" : "manualPrice.sourceAuto"),
          })}
        </p>
      )}

      <div className="holding-columns">
        <section className="card" aria-labelledby={formHeading}>
          <h2 id={formHeading} className="card-label">
            {editing ? t("manualPrice.editPrice", { date: date(editing.day) }) : t("manualPrice.newPrice")}
          </h2>
          <form onSubmit={submit} noValidate>
            <FormError error={issues.length > 0 ? null : failed} />
            <Field
              label={t("manualPrice.day")}
              type="date"
              value={priceDay}
              max={editing ? undefined : today()}
              readOnly={editing !== null}
              onChange={(e) => {
                if (!editing) change(setDay)(e.target.value);
              }}
              hint={replaced ? t("manualPrice.replaces", { date: date(day) }) : undefined}
              error={dayError}
              inputRef={dayRef}
            />
            <DecimalField
              label={unitLabel}
              value={close}
              onChange={change(setClose)}
              onBlur={() => {}}
              error={closeError}
              inputRef={closeRef}
            />
            <p className="muted small">{t("manualPrice.help")}</p>
            <div className="actions">
              {editing && (
                <button type="button" className="button" onClick={startNew}>
                  {t("common.cancel")}
                </button>
              )}
              <button type="submit" className="button button-primary" disabled={save.isPending}>
                {t("manualPrice.save")}
              </button>
            </div>
          </form>
        </section>

        <section className="card table-card" aria-labelledby={listHeading}>
          <h2 id={listHeading} className="card-label">
            {t("manualPrice.list")}
          </h2>
          <p className="muted small">{t("manualPrice.listNote")}</p>
          {prices.isPending ? (
            <Loading />
          ) : prices.isError ? (
            <LoadError error={prices.error} onRetry={() => void prices.refetch()} />
          ) : list.length === 0 ? (
            <p className="muted">{t("manualPrice.empty")}</p>
          ) : (
            <>
              <div className="table-scroll">
                <table className="data" aria-labelledby={listHeading} ref={tableRef} tabIndex={-1}>
                  <thead>
                    <tr>
                      <th scope="col">{t("manualPrice.day")}</th>
                      <th scope="col" className="num">
                        {t("manualPrice.columnPrice")}
                      </th>
                      <th scope="col">
                        <span className="visually-hidden">{t("manualPrice.actions")}</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((price) => (
                      <tr key={price.id}>
                        <td className="nowrap">{date(price.day)}</td>
                        <td className="num nowrap">{quotePrice(t, asset, price.close, locale)}</td>
                        <td>
                          <div className="row-actions">
                            <button
                              type="button"
                              className="link-button"
                              aria-label={t("manualPrice.editRow", { date: date(price.day) })}
                              disabled={save.isPending}
                              onClick={() => edit(price)}
                            >
                              {t("manualPrice.edit")}
                            </button>
                            <button
                              type="button"
                              className="link-button"
                              aria-label={t("manualPrice.deleteRow", { date: date(price.day) })}
                              disabled={save.isPending}
                              onClick={() => setDeleting(price)}
                            >
                              {t("manualPrice.delete")}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {list.length > shown.length && (
                <button type="button" className="button" onClick={() => setShowAll(true)}>
                  {t("manualPrice.showAll", { count: list.length })}
                </button>
              )}
            </>
          )}
        </section>
      </div>

      {deleting && (
        <DeletePriceDialog
          asset={asset}
          price={deleting}
          onClose={() => setDeleting(null)}
          onDone={(text) => {
            if (editing?.id === deleting.id) startNew();
            deletedId.current = deleting.id;
            setDeleting(null);
            void afterPriceChange(client, asset.id);
            announce(text);
          }}
        />
      )}
    </>
  );
}

type DeleteProps = {
  asset: Asset;
  price: ManualPrice;
  onClose: () => void;
  onDone: (notice: string) => void; // deleted, or already gone
};

function DeletePriceDialog({ asset, price, onClose, onDone }: DeleteProps) {
  const { t, i18n } = useTranslation();
  const date = formatDay(price.day, i18n.language);
  const deleting = useRef(false); // one request per click, as for a save
  const remove = useMutation({
    onSettled: () => {
      deleting.current = false;
    },
    mutationFn: () => api<void>("DELETE", `/assets/${asset.id}/prices/${price.id}`),
    onSuccess: () => onDone(t("manualPrice.deleted", { date })),
    onError: (error) => {
      // Deleted meanwhile (another tab): nothing left to do but say so.
      if (error instanceof ApiError && error.code === "NOT_FOUND") onDone(t("manualPrice.gone"));
    },
  });
  const failed = remove.error instanceof ApiError && remove.error.code === "NOT_FOUND" ? null : remove.error;

  return (
    <Dialog danger title={t("manualPrice.deleteTitle", { date })} onClose={onClose}>
      <p>{t("manualPrice.deleteBody")}</p>
      <FormError error={failed} />
      <div className="actions">
        <button type="button" className="button" onClick={onClose}>
          {t("common.cancel")}
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={remove.isPending}
          onClick={() => {
            if (remove.isPending || deleting.current) return;
            deleting.current = true;
            remove.mutate();
          }}
        >
          {t("manualPrice.deleteConfirm")}
        </button>
      </div>
    </Dialog>
  );
}
