import { type DragEvent, type FormEvent, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useAccountInView } from "../accounts";
import { ApiError } from "../api/client";
import type { Account, ImportFormat, ImportPreview } from "../api/types";
import { FormError } from "../components/FormError";
import { errorText } from "../errors";
import { PageTitle } from "../components/PageTitle";
import { formatQuantity } from "../format/decimal";
import { fitsUpload, readFile, useImport } from "../imports";
import { AccountFormDialog } from "./AccountDialogs";
import { ImportDone } from "./ImportDone";
import {
  AccountMapping,
  AssetChoices,
  Blocking,
  BusyWait,
  ConfirmBar,
  Counts,
  Lines,
  Warnings,
} from "./ImportPreview";

type Flow = ReturnType<typeof useImport>;
type Step = "file" | "account" | "preview" | "done";
const STEPS: readonly Step[] = ["file", "account", "preview", "done"];

// The errors that ask the user for a Directa file's account (D-113, D-142).
const ACCOUNT_ERRORS = new Set(["IMPORT_ACCOUNT_REQUIRED", "IMPORT_ACCOUNT_CURRENCY"]);

function codeOf(error: unknown): string | null {
  return error instanceof ApiError ? error.code : null;
}

function stepOf(flow: Flow): Step {
  if (flow.done) return "done";
  if (flow.file === null) return "file";
  if (!flow.preview && ACCOUNT_ERRORS.has(codeOf(flow.error) ?? "")) return "account";
  return "preview";
}

/** Mockups 07 to 09: from the file to the summary (D-142). */
export default function Import() {
  const { t } = useTranslation();
  const flow = useImport();
  const step = stepOf(flow);
  return (
    <>
      <div className="page-head">
        <PageTitle title={t("import.heading")} step={step} />
        <p className="muted">{t(`import.intro.${step}`)}</p>
      </div>
      <ol className="steps" aria-label={t("import.steps")}>
        {STEPS.map((s, index) => {
          const passed = STEPS.indexOf(step) > index;
          return (
            <li key={s} aria-current={s === step ? "step" : undefined}>
              {index + 1}. {t(`import.step.${s}`)}
              {passed && <span className="visually-hidden"> {t("import.stepDone")}</span>}
            </li>
          );
        })}
      </ol>
      {step === "file" && <FileStep flow={flow} />}
      {step === "account" && <AccountStep flow={flow} />}
      {step === "preview" && <PreviewStep flow={flow} />}
      {step === "done" && flow.done && (
        <ImportDone done={flow.done} preview={flow.preview} onAnother={flow.reset} />
      )}
    </>
  );
}

const EUR = "EUR";

function eurAccounts(accounts: Account[]): Account[] {
  return accounts.filter((a) => a.currency === EUR);
}

/** Mockup 07: the file, its format and the "do not move cash" option. */
function FileStep({ flow }: { flow: Flow }) {
  const { t, i18n } = useTranslation();
  const { search } = useAccountInView();
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<"tooLarge" | "readFailed" | "noFile" | null>(null);
  const [format, setFormat] = useState<ImportFormat | "">("");
  const [moveCash, setMoveCash] = useState(true);
  const [reading, setReading] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const formatId = useId();

  const choose = (chosen: File | undefined) => {
    if (!chosen) return;
    if (!fitsUpload(chosen)) {
      setFile(null); // never read: it could not be imported
      setProblem("tooLarge");
      return;
    }
    setFile(chosen);
    setProblem(null);
  };

  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    choose(event.dataTransfer.files[0]);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (file === null) {
      setProblem((current) => current ?? "noFile");
      return;
    }
    setReading(true);
    try {
      const loaded = await readFile(file);
      flow.start(loaded, { format: format || null, moveCash });
    } catch {
      setProblem("readFailed");
    } finally {
      setReading(false);
    }
  };

  // Whole kilobytes, for the eye only (the limit is checked on the exact size).
  const kilobytes = file ? String(Math.max(1, Math.ceil(file.size / 1024))) : "";
  const size = file ? `${formatQuantity(kilobytes, i18n.language)} kB` : "";

  return (
    <form className="card import-file" onSubmit={submit} noValidate>
      <div
        className="drop-zone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={drop}
      >
        <p className="drop-title">{t("import.drop")}</p>
        <p>
          {t("import.or")}{" "}
          <button type="button" className="link-button" onClick={() => input.current?.click()}>
            {t("import.choose")}
          </button>
        </p>
        <p className="muted small">{t("import.accepted")}</p>
        <input
          ref={input}
          type="file"
          accept=".xlsx,.csv,text/csv"
          className="visually-hidden"
          tabIndex={-1}
          aria-label={t("import.fileInput")}
          onChange={(event) => {
            choose(event.target.files?.[0]);
            event.target.value = ""; // the same file can be chosen again
          }}
        />
        {file && <p className="drop-chosen">{t("import.chosen", { name: file.name, size })}</p>}
      </div>
      <div role="alert" className={problem ? "alert" : undefined}>
        {problem ? t(`import.${problem}`) : null}
      </div>

      <div className="form-grid">
        <div className="field">
          <label htmlFor={formatId}>{t("import.format")}</label>
          <select
            id={formatId}
            value={format}
            onChange={(event) => setFormat(event.target.value as ImportFormat | "")}
          >
            <option value="">{t("import.formats.auto")}</option>
            <option value="directa-xlsx">{t("import.formats.directa-xlsx")}</option>
            <option value="app-csv">{t("import.formats.app-csv")}</option>
          </select>
        </div>
      </div>
      <MoveCash checked={!moveCash} onChange={(off) => setMoveCash(!off)} />
      <p className="muted small">{t("import.supported")}</p>
      <Help />

      <div className="form-actions">
        <div className="actions push-left">
          <Link className="button" to={{ pathname: "/transactions", search }}>
            {t("import.cancel")}
          </Link>
          <button type="submit" className="button button-primary" disabled={reading}>
            {t("import.continue")}
          </button>
        </div>
      </div>
      {reading && (
        <p role="status" className="muted loading">
          <span className="spinner" aria-hidden="true" /> {t("import.reading")}
        </p>
      )}
    </form>
  );
}

function MoveCash({ checked, onChange }: { checked: boolean; onChange: (on: boolean) => void }) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div className="check">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        aria-describedby={`${id}-hint`}
        onChange={(event) => onChange(event.target.checked)}
      />
      <label htmlFor={id}>{t("import.moveCash")}</label>
      <span id={`${id}-hint`} className="field-hint">
        {t("import.moveCashHint")}
      </span>
    </div>
  );
}

/** The known limits of the formats (import/directa.md §10, app-csv.md, D-115, D-142). */
function Help() {
  const { t } = useTranslation();
  return (
    <details className="import-help">
      <summary>{t("import.help.title")}</summary>
      <h3>{t("import.help.directa")}</h3>
      <ul>
        <li>{t("import.help.directaNotYet")}</li>
        <li>{t("import.help.directaExpenses")}</li>
        <li>{t("import.help.directaBonds")}</li>
        <li>{t("import.help.directaReview")}</li>
      </ul>
      <h3>{t("import.help.csv")}</h3>
      <ul>
        <li>{t("import.help.csvNames")}</li>
        <li>{t("import.help.csvCurrency")}</li>
      </ul>
      <p>{t("import.help.size")}</p>
    </details>
  );
}

/** While the server is busy (IMPORT_BUSY), or a preview is on its way. */
function Progress({ flow, label }: { flow: Flow; label: string }) {
  // A new wait starts its own countdown from now.
  if (flow.wait) {
    return (
      <p role="status" className="notice-warning">
        <BusyWait key={flow.wait.until} wait={flow.wait} />
      </p>
    );
  }
  if (!flow.pending) return <div role="status" />;
  return (
    <p role="status" className="muted loading">
      <span className="spinner" aria-hidden="true" /> {label}
    </p>
  );
}

/** The error of a file that cannot be previewed, with what the user can do. */
function FileError({ flow }: { flow: Flow }) {
  const { t } = useTranslation();
  const error = flow.error;
  if (!error) return null;
  const extra = error instanceof ApiError ? error.extra : {};
  return (
    <div className="alert" role="alert">
      <p>{errorText(t, error)}</p>
      {typeof extra.column === "string" && (
        <p>{t("import.fileColumn", { column: extra.column })}</p>
      )}
      {typeof extra.line === "string" && <p>{t("import.fileLineNumber", { line: extra.line })}</p>}
      <div className="actions">
        <button type="button" className="button" onClick={flow.reset}>
          {t("import.change")}
        </button>
        <button type="button" className="button" onClick={flow.retry}>
          {t("common.retry")}
        </button>
      </div>
    </div>
  );
}

/** "Conto": the account of a Directa file that no account remembers yet (directa.md §4). */
function AccountStep({ flow }: { flow: Flow }) {
  const { t } = useTranslation();
  const { account: inView, accounts } = useAccountInView();
  const eur = eurAccounts(accounts);
  const [chosen, setChosen] = useState(inView?.currency === EUR ? inView.id : "");
  const [creating, setCreating] = useState(false);
  const [missing, setMissing] = useState(false);
  const id = useId();
  const currencyError = codeOf(flow.error) === "IMPORT_ACCOUNT_CURRENCY";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!chosen) setMissing(true);
    else flow.setAccount(chosen);
  };

  return (
    <>
      <form className="card" onSubmit={submit} noValidate>
        <p>{t("import.accountQuestion")}</p>
        {currencyError && <FormError error={flow.error} />}
        {eur.length === 0 ? (
          <p>{t("import.noEurAccount")}</p>
        ) : (
          <div className="field">
            <label htmlFor={id}>{t("import.accountLabel")}</label>
            <select
              id={id}
              value={chosen}
              aria-describedby={`${id}-hint`}
              aria-invalid={missing ? true : undefined}
              onChange={(event) => {
                setChosen(event.target.value);
                setMissing(false);
              }}
            >
              <option value="">{t("import.accountChoose")}</option>
              {eur.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
            <span id={`${id}-hint`} className="field-hint">
              {t("import.onlyEur")}
            </span>
            {missing && <span className="field-error">{t("errors.IMPORT_ACCOUNT_REQUIRED")}</span>}
          </div>
        )}
        <button type="button" className="link-button" onClick={() => setCreating(true)}>
          {t("import.newAccount")}
        </button>
        <div className="form-actions">
          <div className="actions push-left">
            <button type="button" className="button" onClick={flow.reset}>
              {t("import.back")}
            </button>
            <button type="submit" className="button button-primary">
              {t("common.continue")}
            </button>
          </div>
        </div>
      </form>
      {/* Outside the form: a form inside another one is not valid HTML, and the browser
          submitted the dialog's form natively, reloading the page (no account created). */}
      {creating && (
        <AccountFormDialog
          defaultCurrency={EUR}
          fixedCurrencyHint={t("import.newAccountEur")}
          onClose={() => setCreating(false)}
          onSaved={(saved) => {
            setCreating(false);
            setChosen(saved.id);
          }}
        />
      )}
    </>
  );
}

/** Mockup 08: the preview. */
function PreviewStep({ flow }: { flow: Flow }) {
  const { t } = useTranslation();
  const cashId = useId();
  const preview = flow.preview;
  if (preview === null) {
    return (
      <section className="card">
        <Progress flow={flow} label={t("import.preparing")} />
        <FileError flow={flow} />
      </section>
    );
  }
  return (
    <>
      {/* While a confirmation is on its way, the preview it confirms cannot change: only the
          confirm bar stays live. */}
      <div inert={flow.confirming}>
        <PreviewHeader flow={flow} preview={preview} />
        {flow.error ? <FileError flow={flow} /> : null}
        <Counts preview={preview} />
        <Blocking preview={preview} />
        <AccountMapping flow={flow} preview={preview} />
        <section className="card" aria-labelledby={cashId}>
          <h2 id={cashId}>{t("importPreview.cash")}</h2>
          <MoveCash checked={!flow.choices.moveCash} onChange={(off) => flow.setMoveCash(!off)} />
        </section>
        <AssetChoices flow={flow} preview={preview} />
        <Warnings preview={preview} />
        <Lines flow={flow} preview={preview} />
      </div>
      <ConfirmBar flow={flow} preview={preview} />
    </>
  );
}

function PreviewHeader({ flow, preview }: { flow: Flow; preview: ImportPreview }) {
  const { t } = useTranslation();
  const { accounts } = useAccountInView();
  const id = useId();
  const directa = preview.format === "directa-xlsx";
  const target = preview.accounts[0]?.account_id ?? "";
  return (
    <section className="card import-head">
      <div>
        <p className="import-file-name">{preview.file_name ?? "—"}</p>
        <p className="muted small">
          {t("import.fileLine", {
            format: t(`import.formats.${preview.format}`),
            count: preview.rows_read,
          })}
        </p>
      </div>
      {directa && (
        <div className="field">
          <label htmlFor={id}>{t("import.accountLabel")}</label>
          <select id={id} value={target} onChange={(event) => flow.setAccount(event.target.value)}>
            {eurAccounts(accounts).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
      )}
    </section>
  );
}

