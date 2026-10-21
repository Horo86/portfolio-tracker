import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import type { TFunction } from "i18next";

import { useAccountInView } from "../accounts";
import { api } from "../api/client";
import type {
  Asset,
  ChangeOut,
  FieldIssue,
  Preview,
  QuantityConflict,
  TransactionFields,
  TransactionType,
} from "../api/types";
import { Field, SelectField } from "../components/Field";
import { FormError } from "../components/FormError";
import { Notice, useNotice } from "../components/Notice";
import { PageTitle } from "../components/PageTitle";
import { issueText } from "../errors";
import { formatMoney, formatPrice, formatQuantity } from "../format/decimal";
import {
  afterChange,
  conflictOf,
  issuesOf,
  previewNow,
  TRANSACTION_TYPES,
  useAssets,
  usePreview,
} from "../transactions";
import { ConflictDialog, useWarningTexts } from "./TransactionDialogs";
import {
  assetChoices,
  buildTransaction,
  EMPTY_INPUTS,
  type FieldName,
  fieldOfIssue,
  type Inputs,
  type Layout,
  layoutOf,
  type Problem,
} from "./transactionDraft";

/** Today in the browser's time zone, as the date input writes it. */
function today(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** Mockup 05, "Nuova transazione". */
export default function NewTransaction() {
  const { t } = useTranslation();
  return (
    <>
      <PageTitle title={t("transactionForm.newHeading")} />
      <p className="muted page-intro">{t("transactionForm.intro")}</p>
      <TransactionForm initial={{ ...EMPTY_INPUTS, date: today() }} />
    </>
  );
}

type FormProps = {
  initial: Inputs;
  replaces?: string; // the transaction being edited
  keep?: Partial<TransactionFields>; // stored fields the form does not show (edit page)
  back?: string; // the list's query string to go back to; default: the account in view
  onDelete?: () => void;
};

// What a trade's gross is computed from (§2): when one changes, a stored gross is dropped.
const GROSS_FROM: ReadonlySet<keyof Inputs> = new Set(["quantity", "price", "asset"]);

/**
 * The form of mockup 05 with its live summary (D-139): what is typed is read per language
 * (D-140) and sent to the server's dry run, which completes the amounts and computes the
 * figures; Save sends exactly the completed transaction of a fresh dry run of the input.
 */
export function TransactionForm({ initial, replaces, keep, back, onDelete }: FormProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const client = useQueryClient();
  const navigate = useNavigate();
  const { account: inView, accounts, search } = useAccountInView();
  const assets = useAssets();
  const [typed, setInputs] = useState<Inputs>(initial);
  const [touched, setTouched] = useState<ReadonlySet<FieldName>>(new Set());
  const [submitted, setSubmitted] = useState(false);
  const [conflict, setConflict] = useState<QuantityConflict | null>(null);
  const [focusInvalid, setFocusInvalid] = useState(0);
  // Edit page: the stored gross is kept as it is until what it comes from changes.
  const [storedGross, setStoredGross] = useState(replaces !== undefined && initial.gross !== "");
  const [grossRecomputed, setGrossRecomputed] = useState(false);
  const { notice, announce } = useNotice();
  const noticeRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const warningTexts = useWarningTexts();

  // The account in view, or the only account, until the user picks one.
  const fallback = inView?.id ?? (accounts.length === 1 ? accounts[0].id : "");
  const choices = assetChoices(typed.type, assets.data ?? []);
  const inputs = {
    ...typed,
    account: typed.account || fallback,
    // A security the type cannot refer to (a coupon of a share) is not chosen.
    asset: choices.some((a) => a.id === typed.asset) ? typed.asset : "",
  };

  const account = accounts.find((a) => a.id === inputs.account);
  const asset = choices.find((a) => a.id === inputs.asset);
  const layout = layoutOf(inputs.type, asset, account);
  const built = buildTransaction(inputs, layout, locale);
  const previewBody = built.body
    ? { transaction: { ...built.body, ...keep }, ...(replaces ? { replaces } : {}) }
    : null;
  const backTo = { pathname: "/transactions", search: back ?? search };
  const preview = usePreview(previewBody);

  const save = useMutation({
    mutationFn: async (another: boolean) => {
      const checked = await previewNow(client, previewBody!);
      const out = replaces
        ? await api<ChangeOut>("PUT", `/transactions/${replaces}`, checked.transaction)
        : await api<ChangeOut>("POST", "/transactions", checked.transaction);
      return { out, another };
    },
    onSuccess: ({ out, another }) => {
      void afterChange(client);
      const saved = t(replaces ? "transactionForm.edited" : "transactionForm.saved");
      const text = [saved, ...warningTexts(out.warnings)].join(" ");
      if (!another) {
        navigate(backTo, { state: { notice: text } });
        return;
      }
      // "Save and add another": the type, account, security and date stay.
      setInputs((current) => ({
        ...EMPTY_INPUTS,
        type: current.type,
        account: current.account,
        asset: current.asset,
        date: current.date,
      }));
      setTouched(new Set());
      setSubmitted(false);
      announce(text);
      noticeRef.current?.focus();
    },
    onError: (error) => {
      const found = conflictOf(error);
      if (found) setConflict(found);
      else setFocusInvalid((n) => n + 1);
    },
  });

  // After a refused Save the first field with a problem takes the focus.
  useEffect(() => {
    if (focusInvalid === 0) return;
    formRef.current?.querySelector<HTMLElement>("[aria-invalid=true]")?.focus();
  }, [focusInvalid]);

  const update = <K extends keyof Inputs>(name: K, value: Inputs[K]) => {
    // A stored gross no longer matches: the server computes it again (or, for a security
    // quoted in another currency, the broker's amount is asked for again).
    const recompute = storedGross && layout.kind === "trade" && GROSS_FROM.has(name);
    setInputs((current) => ({ ...current, [name]: value, ...(recompute ? { gross: "" } : {}) }));
    if (recompute || name === "gross") {
      setStoredGross(false);
      setGrossRecomputed(recompute);
    }
    if (!save.isPending) save.reset();
  };
  const blur = (name: FieldName) => setTouched((current) => new Set(current).add(name));

  // Issues from the server: of the Save that failed, else of the dry run of this input.
  const serverError = save.error ?? (preview.current ? preview.error : null);
  const issues = issuesOf(serverError);
  const issueAt = new Map<FieldName, string>();
  const general: FieldIssue[] = [];
  for (const issue of issues) {
    const field = fieldOfIssue(issue.field, inputs.type);
    if (field && layout.fields.has(field) && !issueAt.has(field)) issueAt.set(field, issue.code);
    else if (!field || !layout.fields.has(field)) general.push(issue);
  }

  const problemText = (problem: Problem) =>
    problem === "required" ? t("validation.required") : t(`input.${problem}`);
  const errorOf = (name: FieldName): string | undefined => {
    const problem = built.problems[name];
    if (problem && (submitted || (problem !== "required" && touched.has(name)))) {
      return problemText(problem);
    }
    const code = issueAt.get(name);
    return code ? issueText(t, code) : undefined;
  };

  const submit = (event: { preventDefault: () => void }, another = false) => {
    event.preventDefault();
    if (save.isPending) return; // a second press while saving
    setSubmitted(true);
    if (!previewBody) {
      setFocusInvalid((n) => n + 1);
      return;
    }
    save.mutate(another);
  };

  const otherOpen =
    grossRecomputed ||
    ["gross", "accrued", "taxes"].some(
      (name) => inputs[name as FieldName] !== "" || errorOf(name as FieldName),
    );
  const accountCurrency = account?.currency ?? "";
  // Shared by the decimal fields: value, change, leaving the field and its error.
  const decimal = (name: FieldName & keyof Inputs) => ({
    value: inputs[name] as string,
    onChange: (value: string) => update(name, value),
    onBlur: () => blur(name),
    error: errorOf(name),
  });
  let assetHint: string | undefined;
  if (assets.data?.length === 0) assetHint = t("transactionForm.noAssets");
  else if (assets.data && choices.length === 0) assetHint = t("transactionForm.noBonds");

  return (
    <form ref={formRef} onSubmit={(e) => submit(e)} noValidate>
      <Notice notice={notice} ref={noticeRef} />
      <section className="card form-section">
        <TypeChoice value={inputs.type} onChange={(type) => update("type", type)} />
      </section>

      <div className="tx-layout">
        <section className="card form-section" aria-labelledby="tx-details">
          <h2 id="tx-details">{t("transactionForm.details")}</h2>
          <div className="form-grid">
            <SelectField
              label={t("transactionForm.account")}
              value={inputs.account}
              onChange={(e) => update("account", e.target.value)}
              onBlur={() => blur("account")}
              error={errorOf("account")}
            >
              <option value="">{t("transactionForm.choose")}</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {t("transactionForm.accountOption", { name: a.name, currency: a.currency })}
                </option>
              ))}
            </SelectField>
            {layout.fields.has("asset") && (
              <SelectField
                label={t("transactionForm.asset")}
                value={inputs.asset}
                onChange={(e) => update("asset", e.target.value)}
                onBlur={() => blur("asset")}
                error={errorOf("asset")}
                hint={assetHint}
              >
                <option value="">{t("transactionForm.choose")}</option>
                {[...choices]
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
            )}
          </div>
          <div className="form-grid form-grid-3">
            <Field
              label={t("transactionForm.date")}
              type="date"
              value={inputs.date}
              onChange={(e) => update("date", e.target.value)}
              error={errorOf("date")}
            />
            <Field
              label={t("transactionForm.time")}
              type="time"
              value={inputs.time}
              onChange={(e) => update("time", e.target.value)}
            />
            <Field
              label={t("transactionForm.valueDate")}
              type="date"
              value={inputs.valueDate}
              onChange={(e) => update("valueDate", e.target.value)}
            />
          </div>

          {layout.kind === "trade" && (
            <div className="form-grid form-grid-3">
              <DecimalField
                label={
                  asset?.asset_type === "BOND"
                    ? t("transactionForm.nominal", { currency: asset.quote_currency })
                    : t("transactionForm.quantity")
                }
                {...decimal("quantity")}
              />
              <DecimalField label={priceLabel(t, asset)} {...decimal("price")} />
              <DecimalField
                label={t("transactionForm.fees", { currency: accountCurrency })}
                {...decimal("fees")}
              />
            </div>
          )}
          {layout.grossRequired && (
            <DecimalField
              label={t("transactionForm.gross", { currency: accountCurrency })}
              hint={t("transactionForm.grossForeign", {
                quote: asset?.quote_currency,
                currency: accountCurrency,
              })}
              {...decimal("gross")}
            />
          )}
          {layout.kind === "income" && (
            <div className="form-grid">
              <DecimalField
                label={t("transactionForm.gross", { currency: accountCurrency })}
                {...decimal("gross")}
              />
              <DecimalField
                label={t("transactionForm.taxesWithheld", { currency: accountCurrency })}
                hint={t("transactionForm.taxesWithheldHint")}
                {...decimal("taxes")}
              />
            </div>
          )}
          {layout.fields.has("quantity") && layout.kind === "income" && (
            <details
              className="other-amounts"
              open={inputs.quantity !== "" || errorOf("quantity") ? true : undefined}
            >
              <summary>{t("transactionForm.otherAmountsGross")}</summary>
              <DecimalField
                label={t("transactionForm.quantity")}
                hint={t("transactionForm.dividendQuantityHint")}
                {...decimal("quantity")}
              />
            </details>
          )}
          {layout.kind === "split" && (
            <SplitFields newField={decimal("splitNew")} oldField={decimal("splitOld")} />
          )}
          {layout.kind === "cash" && (
            <div className="form-grid">
              <DecimalField
                label={t("transactionForm.amount", { currency: accountCurrency })}
                {...decimal("amount")}
              />
            </div>
          )}

          {layout.kind === "trade" && (
            <details className="other-amounts" open={otherOpen || undefined}>
              <summary>
                {asset?.asset_type === "BOND" || layout.fields.has("taxes")
                  ? t("transactionForm.otherAmounts")
                  : t("transactionForm.otherAmountsGross")}
              </summary>
              {!layout.grossRequired && (
                <DecimalField
                  label={t("transactionForm.gross", { currency: accountCurrency })}
                  hint={t(
                    grossRecomputed
                      ? "transactionForm.grossRecomputed"
                      : "transactionForm.grossHint",
                  )}
                  {...decimal("gross")}
                />
              )}
              {layout.fields.has("accrued") && (
                <DecimalField
                  label={t(
                    inputs.type === "BUY"
                      ? "transactionForm.accruedPaid"
                      : "transactionForm.accruedReceived",
                    { currency: accountCurrency },
                  )}
                  hint={t("transactionForm.accruedHint")}
                  {...decimal("accrued")}
                />
              )}
              {layout.fields.has("taxes") && (
                <DecimalField
                  label={t("transactionForm.taxes", { currency: accountCurrency })}
                  hint={t(
                    inputs.type === "BUY"
                      ? "transactionForm.taxesBuyHint"
                      : "transactionForm.taxesHint",
                  )}
                  {...decimal("taxes")}
                />
              )}
            </details>
          )}

          {layout.cashOption && (
            <div className="check">
              <input
                id="tx-no-cash"
                type="checkbox"
                checked={inputs.noCash}
                onChange={(e) => update("noCash", e.target.checked)}
                aria-describedby="tx-no-cash-hint"
              />
              <label htmlFor="tx-no-cash">{t("transactionForm.noCash")}</label>
              <span id="tx-no-cash-hint" className="field-hint">
                {t("transactionForm.noCashHint")}
              </span>
            </div>
          )}
          <NotesField value={inputs.notes} onChange={(value) => update("notes", value)} />
        </section>

        <Summary
          preview={preview}
          ready={built.body !== null}
          read={built.read}
          layout={layout}
          inputs={inputs}
          asset={asset}
          generalIssues={general}
          serverError={serverError}
          editing={replaces}
        />
      </div>

      <div className="card form-actions">
        <div className="actions">
          {onDelete && (
            <button
              type="button"
              className="button button-danger-quiet push-left"
              onClick={onDelete}
            >
              {t("transactionForm.delete")}
            </button>
          )}
          <Link to={backTo} className="button">
            {t("common.cancel")}
          </Link>
          {!replaces && (
            <button
              type="button"
              className="button button-soft"
              onClick={(e) => submit(e, true)}
            >
              {t("transactionForm.saveAnother")}
            </button>
          )}
          {/* Never disabled: a disabled button loses the focus, which the conflict dialog
              gives back to it when it closes. */}
          <button type="submit" className="button button-primary">
            {t("transactionForm.save")}
          </button>
        </div>
      </div>

      {conflict && (
        <ConflictDialog
          conflict={conflict}
          own={{ type: inputs.type, date: inputs.date, quantity: built.read.quantity }}
          ownId={replaces}
          asset={asset}
          onClose={() => setConflict(null)}
        />
      )}
    </form>
  );
}

function priceLabel(t: TFunction, asset?: Asset): string {
  if (!asset) return t("transactionForm.price");
  if (asset.quote_unit === "PERCENT_OF_NOMINAL") return t("transactionForm.pricePercent");
  if (asset.quote_unit === "MINOR_CURRENCY_UNIT") {
    return t("transactionForm.priceMinor", { currency: asset.quote_currency });
  }
  return t("transactionForm.priceIn", { currency: asset.quote_currency });
}

function TypeChoice({
  value,
  onChange,
}: {
  value: TransactionType;
  onChange: (type: TransactionType) => void;
}) {
  const { t } = useTranslation();
  const hintId = useId();
  return (
    <fieldset className="type-choice" aria-describedby={hintId}>
      <legend>{t("transactionForm.what")}</legend>
      <div className="type-options">
        {TRANSACTION_TYPES.map((type) => (
          <label key={type} className="type-option">
            <input
              type="radio"
              name="tx-type"
              value={type}
              checked={value === type}
              onChange={() => onChange(type)}
            />
            <span>{t(`types.${type}`)}</span>
          </label>
        ))}
      </div>
      <p id={hintId} className="muted small">
        {t(`typeHints.${value}`)}
      </p>
    </fieldset>
  );
}

type DecimalFieldProps = {
  label: string;
  hint?: string;
  value: string;
  error?: string;
  onChange: (value: string) => void;
  onBlur: () => void;
};

/** A number typed as text: read per language (D-140), never through a binary float. */
function DecimalField({ label, hint, value, error, onChange, onBlur }: DecimalFieldProps) {
  return (
    <Field
      label={label}
      hint={hint}
      error={error}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onBlur}
      className="num-input"
      inputMode="decimal"
      autoComplete="off"
      spellCheck={false}
    />
  );
}

type DecimalProps = Omit<DecimalFieldProps, "label" | "hint">;

/** STOCK_SPLIT: the ratio new:old (§8), each part typed as a number. */
function SplitFields({ newField, oldField }: { newField: DecimalProps; oldField: DecimalProps }) {
  const { t } = useTranslation();
  const hintId = useId();
  return (
    <fieldset className="split-ratio" aria-describedby={hintId}>
      <legend>{t("transactionForm.ratio")}</legend>
      <div className="form-grid">
        <DecimalField label={t("transactionForm.splitNew")} {...newField} />
        <DecimalField label={t("transactionForm.splitOld")} {...oldField} />
      </div>
      <p id={hintId} className="field-hint">
        {t("transactionForm.ratioHint")}
      </p>
    </fieldset>
  );
}

function NotesField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{t("transactionForm.notes")}</label>
      <textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={2000}
        rows={3}
        placeholder={t("transactionForm.notesPlaceholder")}
      />
    </div>
  );
}

type SummaryProps = {
  preview: ReturnType<typeof usePreview>;
  ready: boolean;
  read: Partial<Record<string, string>>;
  layout: Layout;
  inputs: Inputs;
  asset?: Asset;
  generalIssues: FieldIssue[];
  serverError: unknown;
  editing?: string; // the id of the transaction being edited
};

/** "Riepilogo" (mockup 05): the server's figures for the input as it is now. */
function Summary({
  preview,
  ready,
  read,
  layout,
  inputs,
  asset,
  generalIssues,
  serverError,
  editing,
}: SummaryProps) {
  const { t } = useTranslation();
  const warningTexts = useWarningTexts();
  const conflict = preview.current ? conflictOf(preview.error) : null;
  const shown: Preview | undefined = preview.data;
  const stale = !preview.current;

  let body;
  if (!ready) {
    body = <p className="muted">{t("summary.incomplete")}</p>;
  } else if (conflict) {
    body = <ConflictNote conflict={conflict} quantity={read.quantity} ownId={editing} />;
  } else if (preview.current && preview.error) {
    body = <SummaryError error={preview.error} issues={generalIssues} />;
  } else if (!shown) {
    body = <p className="muted">{t("summary.computing")}</p>;
  } else {
    body = (
      <Figures
        preview={shown}
        read={read}
        layout={layout}
        inputs={inputs}
        asset={asset}
        stale={stale}
        editing={editing !== undefined}
      />
    );
  }
  const warnings = ready && preview.current && shown ? warningTexts(shown.warnings) : [];

  return (
    <aside className="card summary" aria-labelledby="tx-summary">
      <h2 id="tx-summary">{t("summary.heading")}</h2>
      <div aria-busy={ready && stale}>{body}</div>
      {/* Read when they change: the figures themselves are not announced on every key. */}
      <div role="status">
        {stale && ready && shown && <p className="muted small">{t("summary.updating")}</p>}
        {warnings.length > 0 && (
          <ul className="warnings">
            {warnings.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        )}
        {serverError != null && !preview.error && !conflictOf(serverError) && (
          <SummaryError error={serverError} issues={generalIssues} />
        )}
      </div>
      <p className="muted small summary-note">{t("summary.note")}</p>
    </aside>
  );
}

function SummaryError({ error, issues }: { error: unknown; issues: FieldIssue[] }) {
  const { t } = useTranslation();
  if (issuesOf(error).length === 0) return <FormError error={error} />;
  return (
    <div className="alert">
      <p>{t("summary.checkFields")}</p>
      {issues.length > 0 && (
        <ul>
          {issues.map((issue) => (
            <li key={`${issue.field}-${issue.code}`}>{issueText(t, issue.code)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

type ConflictNoteProps = { conflict: QuantityConflict; quantity?: string; ownId?: string };

/** Whether the conflict is about the transaction of the form: new (index 0) or edited. */
export function isOwnConflict(conflict: QuantityConflict, ownId?: string): boolean {
  return conflict.index === 0 || (ownId !== undefined && conflict.transaction_id === ownId);
}

function ConflictNote({ conflict, quantity, ownId }: ConflictNoteProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const values = {
    held: formatQuantity(conflict.held, locale),
    after: formatQuantity(conflict.quantity, locale),
    quantity: quantity ? formatQuantity(quantity, locale) : "",
  };
  const key = isOwnConflict(conflict, ownId) ? "summary.conflictOwn" : "summary.conflictOther";
  return (
    <div className="alert">
      <p>{t(key, values)}</p>
      <p className="small">{t("summary.conflictSave")}</p>
    </div>
  );
}

type FiguresProps = {
  preview: Preview;
  read: Partial<Record<string, string>>;
  layout: Layout;
  inputs: Inputs;
  asset?: Asset;
  stale: boolean;
  editing: boolean;
};

const TOTALS: Partial<Record<TransactionType, string>> = {
  BUY: "summary.totalCost",
  SELL: "summary.proceeds",
  DIVIDEND: "summary.netIncome",
  COUPON: "summary.netIncome",
};

function Figures({ preview, read, layout, inputs, asset, stale, editing }: FiguresProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const money = (value: string, signed = false) =>
    formatMoney(value, preview.currency, locale, { signed });
  const tx = preview.transaction!;
  const bond = asset?.asset_type === "BOND";
  const price = (value: string) =>
    bond ? t("summary.percent", { value: formatPrice(value, locale) }) : formatPrice(value, locale);

  const rows: [string, string][] = [];
  if (layout.kind === "cash") {
    rows.push([t("summary.amount"), money(tx.total_amount!)]);
  } else if (layout.kind === "income") {
    rows.push([t("summary.gross"), money(tx.gross_amount!)]);
    if (tx.taxes) rows.push([t("summary.taxes"), money(tx.taxes)]);
  } else if (layout.kind === "trade") {
    const grossLabel = preview.derived.includes("gross_amount")
      ? t("summary.grossOf", {
          quantity: formatQuantity(read.quantity!, locale),
          price: price(read.price!),
        })
      : t("summary.grossTyped");
    rows.push([grossLabel, money(tx.gross_amount!)]);
    if (tx.fees) rows.push([t("summary.fees"), money(tx.fees)]);
    if (tx.accrued_interest_paid) {
      rows.push([t("summary.accruedPaid"), money(tx.accrued_interest_paid)]);
    }
    if (tx.accrued_interest_received) {
      rows.push([t("summary.accruedReceived"), money(tx.accrued_interest_received)]);
    }
    if (tx.taxes) rows.push([t("summary.taxes"), money(tx.taxes)]);
  }
  const totalKey = TOTALS[inputs.type];
  // A dividend or coupon leaves the quantity as it is: only trades and splits show it.
  const position = layout.kind === "trade" || layout.kind === "split" ? preview.position : null;
  const pmc = position?.pmc_after;
  let cashChange = money(preview.cash.change, true);
  // On an edit the change is the difference from before, which may be zero or not.
  if (!editing && tx.affects_cash === false) cashChange = t("summary.cashNotMoved");

  return (
    <div className={stale ? "figures-stale" : undefined}>
      {(rows.length > 0 || totalKey) && (
        <dl className="summary-rows">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd className="num">{value}</dd>
            </div>
          ))}
          {totalKey && (
            <div className="summary-total">
              <dt>{t(totalKey)}</dt>
              <dd className="num">{money(tx.total_amount!)}</dd>
            </div>
          )}
        </dl>
      )}
      <dl className="summary-rows summary-box">
        {layout.kind !== "split" && (
          <>
            <div>
              <dt>{t(editing ? "summary.cashChangeEdit" : "summary.cashChange")}</dt>
              <dd className="num">{cashChange}</dd>
            </div>
            <div>
              <dt>{t("summary.cashAfter")}</dt>
              <dd className="num">{money(preview.cash.after)}</dd>
            </div>
          </>
        )}
        {position && (
          <>
            <div>
              <dt>{t("summary.quantityAfter")}</dt>
              <dd className="num">{formatQuantity(position.quantity_after, locale)}</dd>
            </div>
            <div>
              <dt>{inputs.type === "SELL" ? t("summary.pmc") : t("summary.newPmc")}</dt>
              <dd className="num">
                {pmc ? (bond ? price(pmc) : formatPrice(pmc, locale, preview.currency)) : "—"}
              </dd>
            </div>
          </>
        )}
      </dl>
    </div>
  );
}
