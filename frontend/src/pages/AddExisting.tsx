import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router";

import type { TFunction } from "i18next";

import { useAccountInView } from "../accounts";
import { api } from "../api/client";
import type { Asset, BatchIn, ChangeOut, FieldIssue } from "../api/types";
import { AddAsset } from "../components/AddAsset";
import { DateField } from "../components/DateField";
import { DecimalField } from "../components/DecimalField";
import { Dialog } from "../components/Dialog";
import { SelectField } from "../components/Field";
import { FormError } from "../components/FormError";
import { Notice, useNotice } from "../components/Notice";
import { PageTitle } from "../components/PageTitle";
import { issueText } from "../errors";
import { formatDay, today } from "../format/date";
import { formatMoney } from "../format/decimal";
import { MAX_AMOUNT_DECIMALS, MAX_QUANTITY_DECIMALS } from "../format/decimalInput";
import { useMe } from "../session";
import {
  afterChange,
  batchPreviewNow,
  conflictOf,
  rowIssuesOf,
  useAssets,
  useBatchPreview,
} from "../transactions";
import {
  buildBatch,
  emptyRow,
  MAX_ROWS,
  type RowField,
  rowFieldOfIssue,
  type RowInput,
  type RowProblem,
  sameAssetRow,
  START_ROWS,
} from "./addExisting";
import { useWarningTexts } from "./TransactionDialogs";

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The unit a row's average cost is typed in: the price currency, its hundredths, or % of
 * nominal for a bond (§2). */
function unitHint(t: TFunction, asset: Asset): string {
  if (asset.quote_unit === "PERCENT_OF_NOMINAL") return t("addExisting.pmcBond");
  if (asset.quote_unit === "MINOR_CURRENCY_UNIT") {
    return t("addExisting.unitMinor", { currency: asset.quote_currency });
  }
  return t("addExisting.unitCurrency", { currency: asset.quote_currency });
}

/**
 * Mockup 10, "Aggiungi titoli che già possiedo" (D-147): what the user held before using the
 * app. Each complete row becomes a BUY that does not move cash, all on one date in one
 * account, saved together or not at all; the totals come from the server's dry run.
 */
export default function AddExisting() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const client = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { account: inView, accounts, search } = useAccountInView();
  const assets = useAssets();
  const me = useMe().data;
  const warningTexts = useWarningTexts();
  const { notice, announce } = useNotice();
  const formRef = useRef<HTMLFormElement>(null);
  const nextKey = useRef(START_ROWS + 1);
  const [chosen, setChosen] = useState(""); // the account picked here; "" until then
  const [date, setDate] = useState(today);
  const [rows, setRows] = useState<RowInput[]>(() =>
    Array.from({ length: START_ROWS }, (_, i) => emptyRow(i + 1, i === 0 ? (params.get("asset") ?? "") : "")),
  );
  const [touched, setTouched] = useState<ReadonlySet<string>>(new Set());
  const [submitted, setSubmitted] = useState(false);
  const [focusInvalid, setFocusInvalid] = useState(0);
  const [newFor, setNewFor] = useState<number | null>(null); // the row whose "New security…" is open

  // The account in view, or the only account, until one is picked; none in "All accounts".
  const accountId = chosen || inView?.id || (accounts.length === 1 ? accounts[0].id : "");
  const account = accounts.find((a) => a.id === accountId);
  const list = assets.data ?? [];
  const batch = buildBatch(rows, list, account, DAY.test(date) ? date : "", locale);
  const preview = useBatchPreview(batch.body);
  const before = params.get("before");

  // Set at the click, before any render: a second click in the same moment sees it, where
  // the mutation's own state may not have reached this closure yet.
  const saving = useRef(false);
  const save = useMutation({
    onSettled: () => {
      saving.current = false;
    },
    mutationFn: async (body: BatchIn) => {
      // Exactly the rows of a fresh dry run of the input (D-139).
      const checked = await batchPreviewNow(body);
      return api<ChangeOut>("POST", "/transactions/batch", { transactions: checked.transactions });
    },
    onSuccess: (out) => {
      void afterChange(client);
      const saved = t("addExisting.saved", { count: out.transactions.length, account: account?.name });
      const text = [saved, ...warningTexts(out.warnings)].join(" ");
      navigate(
        { pathname: "/holdings", search: `?${new URLSearchParams({ account: accountId })}` },
        { state: { notice: text } },
      );
    },
    onError: () => setFocusInvalid((n) => n + 1),
  });

  // After a refused Save the first field with a problem takes the focus.
  useEffect(() => {
    if (focusInvalid === 0) return;
    formRef.current?.querySelector<HTMLElement>("[aria-invalid=true]")?.focus();
  }, [focusInvalid]);

  // While saving the fields are read-only: what is saved is what was there at the click.
  const editRows = (change: (current: RowInput[]) => RowInput[]) => {
    if (save.isPending) return;
    setRows(change);
    save.reset();
  };
  const setField = (key: number, name: RowField, value: string) =>
    editRows((current) => current.map((r) => (r.key === key ? { ...r, [name]: value } : r)));
  const addRow = () =>
    editRows((current) =>
      current.length >= MAX_ROWS ? current : [...current, emptyRow(nextKey.current++)],
    );
  // The last row is emptied rather than removed: there is always one to type in.
  const removeRow = (key: number) =>
    editRows((current) =>
      current.length === 1 ? [emptyRow(nextKey.current++)] : current.filter((r) => r.key !== key),
    );

  // The server's word on the rows: of the Save that failed, else of the dry run of this input.
  const serverError = save.error ?? (preview.current ? preview.error : null);
  const byRow = new Map<number, FieldIssue[]>();
  for (const [index, issues] of rowIssuesOf(serverError)) {
    const row = batch.rowOf[index];
    if (row !== undefined) byRow.set(row, issues);
  }
  const conflict = conflictOf(serverError);
  const shown = preview.current ? preview.data : undefined;

  const problemText = (name: RowField, problem: RowProblem) =>
    problem === "required"
      ? t("addExisting.incomplete")
      : t(`input.${problem}`, {
          max: name === "quantity" ? MAX_QUANTITY_DECIMALS : MAX_AMOUNT_DECIMALS,
        });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (save.isPending || saving.current) return;
    setSubmitted(true);
    if (!account || !DAY.test(date) || batch.incomplete > 0 || !batch.body) {
      setFocusInvalid((n) => n + 1);
      return;
    }
    saving.current = true;
    save.mutate(batch.body);
  };

  const ready = batch.ready;
  const noRows = submitted && ready === 0 && batch.incomplete === 0;
  // The last answer's warnings stay while the next one comes, so the live region is not
  // emptied and announced again on every key; they go when nothing is left to ask about.
  const refused = preview.current && preview.error;
  const lastWarnings = batch.body && preview.data && !refused ? preview.data.warnings : [];
  const warnings = warningTexts(shown?.warnings ?? lastWarnings);
  const conflictRow = conflict?.index !== undefined ? batch.rowOf[conflict.index] : undefined;
  // What no row can show (another user's account or security, a stored transaction in
  // conflict, the server) is said above the footer; after a refused Save, the rows to check.
  const failed = byRow.size === 0 && conflictRow === undefined ? serverError : null;
  const rowsToCheck = save.error
    ? [...new Set([...byRow.keys(), ...(conflictRow !== undefined ? [conflictRow] : [])])]
        .sort((a, b) => a - b)
        .map((row) => row + 1)
    : [];

  return (
    <>
      <PageTitle title={t("addExisting.title")} />
      <p className="muted page-intro">{t("addExisting.intro")}</p>
      {before && DAY.test(before) && (
        <p className="notice">{t("addExisting.fromConflict", { date: formatDay(before, locale) })}</p>
      )}
      <Notice notice={notice} />

      <form ref={formRef} onSubmit={submit} noValidate>
        <section className="card form-section">
          <div className="form-grid">
            <SelectField
              label={t("addExisting.account")}
              value={accountId}
              onChange={(e) => {
                if (save.isPending) return;
                setChosen(e.target.value);
                save.reset();
              }}
              error={submitted && !account ? t("validation.required") : undefined}
            >
              <option value="">{t("transactionForm.choose")}</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {t("transactionForm.accountOption", { name: a.name, currency: a.currency })}
                </option>
              ))}
            </SelectField>
            <DateField
              label={t("addExisting.date")}
              hint={t("addExisting.dateHint")}
              value={date}
              onChange={(value) => {
                if (save.isPending) return;
                setDate(value);
                save.reset();
              }}
              error={submitted && !DAY.test(date) ? t("validation.required") : undefined}
            />
          </div>

          <div className="table-scroll">
            <table className="data add-rows">
              <caption className="visually-hidden">{t("addExisting.caption")}</caption>
              <thead>
                <tr>
                  <th scope="col">
                    <span className="visually-hidden">{t("addExisting.row")}</span>
                  </th>
                  <th scope="col">{t("addExisting.asset")}</th>
                  <th scope="col" className="num">
                    {t("addExisting.quantity")}
                  </th>
                  <th scope="col" className="num">
                    {t("addExisting.pmc")}
                  </th>
                  <th scope="col" className="num">
                    {t("addExisting.total")}
                  </th>
                  <th scope="col">
                    <span className="visually-hidden">{t("addExisting.actions")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => {
                  const n = index + 1;
                  const state = batch.states[index];
                  const asset = list.find((a) => a.id === row.asset);
                  const issues = byRow.get(index) ?? [];
                  const show = submitted && !state.empty;
                  // An issue on no field of the row (the total the server derives for a row in
                  // the account currency) is said at the average cost, the field it comes from.
                  const placeOf = (field: string): RowField => {
                    const place = rowFieldOfIssue(field);
                    return place === null || (place === "total" && !state.foreign) ? "price" : place;
                  };
                  const errorOf = (name: RowField): string | undefined => {
                    const problem = state.problems[name];
                    const seen = show || (problem !== "required" && touched.has(`${row.key}.${name}`));
                    if (problem && seen) return problemText(name, problem);
                    const issue = issues.find((i) => placeOf(i.field) === name);
                    return issue ? issueText(t, issue.code) : undefined;
                  };
                  const at = batch.rowOf.indexOf(index);
                  const total = at !== -1 ? shown?.transactions[at]?.total_amount : null;
                  const same = sameAssetRow(rows, index);
                  const label = (field: string) => t("addExisting.inRow", { field, row: n });
                  const blur = (name: RowField) =>
                    setTouched((current) => new Set(current).add(`${row.key}.${name}`));
                  const decimal = (name: RowField) => ({
                    value: row[name],
                    onChange: (value: string) => setField(row.key, name, value),
                    onBlur: () => blur(name),
                    error: errorOf(name),
                    hideLabel: true,
                  });
                  const hints = [
                    same !== null ? t("addExisting.sameAsset", { row: same + 1 }) : "",
                    state.foreign && asset && account
                      ? t("addExisting.foreignHint", {
                          quote: asset.quote_currency,
                          currency: account.currency,
                        })
                      : "",
                  ].filter(Boolean);
                  const assetHint = hints.length > 0 ? hints.join(" ") : undefined;
                  return (
                    <tr key={row.key}>
                      <th scope="row" className="row-number">
                        {n}
                      </th>
                      <td className="add-asset-cell">
                        <SelectField
                          label={label(t("addExisting.asset"))}
                          hideLabel
                          value={asset ? row.asset : ""}
                          onChange={(e) => setField(row.key, "asset", e.target.value)}
                          error={errorOf("asset")}
                          hint={assetHint}
                        >
                          <option value="">{t("addExisting.chooseAsset")}</option>
                          {[...list]
                            .sort((a, b) => a.name.localeCompare(b.name))
                            .map((a) => (
                              <option key={a.id} value={a.id}>
                                {t("transactionForm.assetOption", {
                                  name: a.name,
                                  currency: a.quote_currency,
                                })}
                              </option>
                            ))}
                        </SelectField>
                        <button
                          type="button"
                          className="link-button add-new-asset"
                          aria-label={t("addExisting.newAssetRow", { row: n })}
                          onClick={() => setNewFor(row.key)}
                        >
                          {t("transactionForm.newAsset")}
                        </button>
                      </td>
                      <td>
                        <DecimalField
                          label={label(
                            asset?.asset_type === "BOND"
                              ? t("addExisting.nominal")
                              : t("addExisting.quantity"),
                          )}
                          hint={
                            asset?.asset_type === "BOND"
                              ? t("addExisting.unitCurrency", { currency: asset.quote_currency })
                              : undefined
                          }
                          {...decimal("quantity")}
                        />
                      </td>
                      <td>
                        <DecimalField
                          label={label(t("addExisting.pmc"))}
                          hint={asset ? unitHint(t, asset) : undefined}
                          {...decimal("price")}
                        />
                      </td>
                      <td className="num">
                        {state.foreign && account ? (
                          <DecimalField
                            label={label(t("addExisting.total"))}
                            hint={t("addExisting.unitCurrency", { currency: account.currency })}
                            {...decimal("total")}
                          />
                        ) : (
                          <span className="add-total">
                            {total && account ? formatMoney(total, account.currency, locale) : "—"}
                          </span>
                        )}
                        {conflictRow === index && (
                          <span className="field-error block">{t("errors.QUANTITY_CONFLICT")}</span>
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="remove-row"
                          aria-label={t("addExisting.removeRow", { row: n })}
                          onClick={() => removeRow(row.key)}
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="add-rows-foot">
            <button
              type="button"
              className="button"
              onClick={addRow}
              disabled={rows.length >= MAX_ROWS}
            >
              {t("addExisting.addRow")}
            </button>
            {rows.length >= MAX_ROWS && <span className="muted small">{t("addExisting.maxRows")}</span>}
          </div>
        </section>

        <div role="status">
          {warnings.length > 0 && (
            <ul className="warnings">
              {warnings.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          )}
          {noRows && <p className="alert">{t("addExisting.noRows")}</p>}
        </div>
        <FormError error={failed} />
        <div role="alert" className={rowsToCheck.length > 0 ? "alert" : undefined}>
          {rowsToCheck.length > 0 &&
            t("addExisting.checkRows", {
              count: rowsToCheck.length,
              rows: rowsToCheck.join(", "),
            })}
        </div>

        <div className="card form-actions add-actions">
          <p className="muted">{t("addExisting.ready", { count: ready })}</p>
          <div className="actions">
            <Link to={{ pathname: "/holdings", search }} className="button">
              {t("common.cancel")}
            </Link>
            {/* Never disabled: a disabled button loses the focus. */}
            <button type="submit" className="button button-primary">
              {t("addExisting.save", { count: ready })}
            </button>
          </div>
        </div>
      </form>

      {/* Outside the form: a form inside another one is not valid HTML. */}
      {newFor !== null && (
        <Dialog wide title={t("assetSearch.title")} onClose={() => setNewFor(null)}>
          <AddAsset
            headingLevel={3}
            currency={account?.currency ?? me?.reporting_currency ?? "EUR"}
            onCancel={() => setNewFor(null)}
            existing={(asset) => (
              <button
                type="button"
                className="button"
                onClick={() => {
                  setField(newFor, "asset", asset.id);
                  setNewFor(null);
                }}
              >
                {t("addExisting.useExisting")}
              </button>
            )}
            onCreated={(asset) => {
              setField(newFor, "asset", asset.id);
              setNewFor(null);
              announce(t("assetSearch.added", { name: asset.name }));
            }}
          />
        </Dialog>
      )}
    </>
  );
}
