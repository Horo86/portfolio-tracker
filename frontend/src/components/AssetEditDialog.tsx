import { type QueryClient, useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ACCOUNTS_KEY } from "../accounts";
import { api, ApiError } from "../api/client";
import type { Asset, AssetType, FieldIssue, QuoteUnit } from "../api/types";
import { issueText } from "../errors";
import { manualPricesKey, useManualPrices } from "../manualPrices";
import { ASSETS_KEY } from "../transactions";
import { isinProblem, normalIsin } from "../validation";
import { ASSET_TYPES, unitsOf } from "./AssetCreateForm";
import { CurrencySelect } from "./CurrencySelect";
import { Dialog } from "./Dialog";
import { Field, SelectField } from "./Field";
import { FormError } from "./FormError";
import { LoadError, Loading } from "./Status";

// The fields a transaction locks (D-91), named in the dialog's order.
const LOCKED_FIELDS = ["asset_type", "quote_currency", "isin", "quote_unit"] as const;

type Props = {
  asset: Asset; // from the user's list: a refetch after a refusal reaches the dialog
  marketData: "ON" | "OFF";
  onClose: () => void;
  onSaved: (asset: Asset) => void;
  onDelete: () => void; // offered only while no transaction uses it (D-91)
};

/**
 * "Modifica titolo" (D-150): the fields of the create form, filled in from the security. While
 * a transaction uses it, type, currency, price unit and ISIN are shown locked and named (D-91);
 * name and symbol stay free.
 */
export function AssetEditDialog({ asset, marketData, onClose, onSaved, onDelete }: Props) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const locked = asset.in_use;
  // What the dialog opened with: only the fields changed from it are sent, so a change made
  // elsewhere meanwhile to another field is not put back.
  const [start] = useState(asset);
  const [name, setName] = useState(asset.name);
  const [type, setType] = useState<AssetType>(asset.asset_type);
  const [currency, setCurrency] = useState(asset.quote_currency);
  const [unit, setUnit] = useState<QuoteUnit>(asset.quote_unit);
  const [isin, setIsin] = useState(asset.isin ?? "");
  const [ticker, setTicker] = useState(asset.ticker ?? "");
  const [submitted, setSubmitted] = useState(false);
  const [isinLeft, setIsinLeft] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const isinRef = useRef<HTMLInputElement>(null);

  // Locked, the fields show what is stored, whatever was typed before a refusal locked them.
  const shown = locked
    ? { type: asset.asset_type, currency: asset.quote_currency, unit: asset.quote_unit, isin: asset.isin ?? "" }
    : { type, currency, unit, isin };
  const isinText = normalIsin(isin);
  // Only a new ISIN is checked here: the stored one passed the server's check.
  const isinBad = locked || isinText === "" || isinText === (start.isin ?? "") ? null : isinProblem(isinText);
  const nameMissing = name.trim() === "";

  // Set when a save starts, before any render: a second click in the same moment sees it,
  // where the mutation's own state may not have reached this closure yet.
  const saving = useRef(false);
  const save = useMutation({
    onSettled: () => {
      saving.current = false;
    },
    mutationFn: (changes: Record<string, string | null>) =>
      api<Asset>("PATCH", `/assets/${asset.id}`, changes),
    onSuccess: (saved) => {
      client.setQueryData<Asset[]>(ASSETS_KEY, (list) =>
        list?.map((a) => (a.id === saved.id ? saved : a)),
      );
      void client.invalidateQueries({ queryKey: ASSETS_KEY });
      void client.invalidateQueries({ queryKey: ACCOUNTS_KEY }); // the holdings carry the name
      onSaved(saved);
    },
    onError: (error) => {
      if (!(error instanceof ApiError)) return;
      // A transaction saved meanwhile: the list says so and the fields lock; the security is
      // now a holding. Deleted meanwhile: the page says so beneath the dialog.
      if (error.code === "ASSET_IN_USE") void afterRefusal(client);
      if (error.code === "NOT_FOUND") void client.invalidateQueries({ queryKey: ASSETS_KEY });
      if (error.code === "ISIN_ALREADY_USED") isinRef.current?.focus();
    },
  });

  function changes(): Record<string, string | null> {
    const out: Record<string, string | null> = {};
    if (name.trim() !== start.name) out.name = name.trim();
    if (ticker.trim() !== (start.ticker ?? "")) out.ticker = ticker.trim();
    if (locked) return out;
    if (type !== start.asset_type) out.asset_type = type;
    if (currency !== start.quote_currency) out.quote_currency = currency;
    if (unit !== start.quote_unit) out.quote_unit = unit;
    if (isinText !== (start.isin ?? "")) out.isin = isinText || null;
    return out;
  }

  const chooseType = (next: AssetType) => {
    setType(next);
    if (!unitsOf(next, unit).includes(unit)) setUnit(unitsOf(next, unit)[0]);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (save.isPending || saving.current) return;
    setSubmitted(true);
    if (nameMissing) nameRef.current?.focus();
    else if (isinBad) isinRef.current?.focus();
    else {
      const body = changes();
      if (Object.keys(body).length === 0) {
        onClose();
        return;
      }
      saving.current = true;
      save.mutate(body);
    }
  };

  // A refusal of one field is shown at the field; anything else above the form.
  const failed = save.error instanceof ApiError ? save.error : null;
  const isinTaken = failed?.code === "ISIN_ALREADY_USED";
  const unitIssue =
    failed?.code === "VALIDATION_FAILED"
      ? (failed.extra.issues as FieldIssue[] | undefined)?.find((i) => i.field === "quote_unit")
      : undefined;
  let alertText: string | undefined;
  if (failed?.code === "ASSET_IN_USE") {
    const refused = (failed.extra.fields as string[] | undefined) ?? [];
    const names = LOCKED_FIELDS.filter((f) => refused.includes(f)).map((f) => t(`assetEdit.fields.${f}`));
    const fields = new Intl.ListFormat(i18n.language, { type: "conjunction" }).format(names);
    alertText = t("assetEdit.inUseFields", { count: names.length, fields });
  }
  let isinError: string | undefined;
  if (isinBad && (submitted || isinLeft)) isinError = t(isinBad);
  else if (isinTaken) isinError = t("errors.ISIN_ALREADY_USED");

  return (
    <Dialog title={t("assetEdit.edit")} onClose={onClose}>
      {locked && <p className="muted">{t("assetEdit.locked")}</p>}
      <form onSubmit={submit} noValidate>
        <FormError error={isinTaken || unitIssue ? null : save.error} text={alertText} />
        <Field
          label={t("assetForm.name")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={200}
          autoComplete="off"
          error={submitted && nameMissing ? t("validation.required") : undefined}
          inputRef={nameRef}
          required
        />
        <div className="form-grid">
          <SelectField
            label={t("assetForm.type")}
            value={shown.type}
            onChange={(e) => chooseType(e.target.value as AssetType)}
            disabled={locked}
          >
            {ASSET_TYPES.map((value) => (
              <option key={value} value={value}>
                {t(`assetTypes.${value}`)}
              </option>
            ))}
          </SelectField>
          <CurrencySelect
            label={t("assetForm.currency")}
            value={shown.currency}
            onChange={setCurrency}
            disabled={locked}
          />
        </div>
        <div className="form-grid">
          <Field
            label={t("assetForm.isin")}
            value={shown.isin}
            onChange={(e) => {
              setIsin(e.target.value);
              if (isinTaken) save.reset();
            }}
            onBlur={() => setIsinLeft(true)}
            maxLength={20}
            autoComplete="off"
            spellCheck={false}
            disabled={locked}
            error={isinError}
            inputRef={isinRef}
          />
          <Field
            label={t("assetForm.ticker")}
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            maxLength={32}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <SelectField
          label={t("assetForm.unit")}
          hint={shown.type === "BOND" ? t("assetForm.unitBond") : undefined}
          value={shown.unit}
          onChange={(e) => setUnit(e.target.value as QuoteUnit)}
          disabled={locked}
          error={unitIssue ? issueText(t, unitIssue.code) : undefined}
        >
          {unitsOf(shown.type, shown.unit).map((value) => (
            <option key={value} value={value}>
              {t(`quoteUnits.${value}`, { currency: shown.currency })}
            </option>
          ))}
        </SelectField>
        {/* Another ISIN or currency is another instrument (D-125): linking starts again. */}
        {marketData === "ON" && !locked && isinText !== "" && (
          <p className="muted small">{t("assetEdit.relookup")}</p>
        )}
        <div className="actions">
          {locked ? (
            <p className="muted small push-left">{t("assetEdit.deleteHint")}</p>
          ) : (
            <button type="button" className="button button-danger-quiet push-left" onClick={onDelete}>
              {t("assetEdit.delete")}
            </button>
          )}
          <button type="button" className="button" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button type="submit" className="button button-primary" disabled={save.isPending}>
            {t("assetEdit.save")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** A transaction saved meanwhile: the security is in use and a holding now. */
function afterRefusal(client: QueryClient): Promise<unknown> {
  return Promise.all([
    client.invalidateQueries({ queryKey: ASSETS_KEY }),
    client.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  ]);
}

type DeleteProps = {
  asset: Asset;
  onClose: () => void;
  onDone: (notice: string) => void; // deleted, or already gone
};

/** "Eliminare «name»?" (D-150): a security no transaction uses, with its manual prices, whose
 * number the confirmation says (from the list of page 12). */
export function DeleteAssetDialog({ asset, onClose, onDone }: DeleteProps) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const prices = useManualPrices(asset.id);
  const deleting = useRef(false); // one request per click, as for a save
  const gone = () => {
    client.setQueryData<Asset[]>(ASSETS_KEY, (list) => list?.filter((a) => a.id !== asset.id));
    client.removeQueries({ queryKey: manualPricesKey(asset.id) });
    void client.invalidateQueries({ queryKey: ASSETS_KEY });
  };
  const remove = useMutation({
    onSettled: () => {
      deleting.current = false;
    },
    mutationFn: () => api<void>("DELETE", `/assets/${asset.id}`),
    onSuccess: () => {
      onDone(t("assetEdit.deleted", { name: asset.name }));
      gone();
    },
    onError: (error) => {
      if (!(error instanceof ApiError)) return;
      // A transaction saved meanwhile: the list says so; deleted meanwhile: nothing left to do.
      if (error.code === "ASSET_IN_USE") void afterRefusal(client);
      if (error.code === "NOT_FOUND") {
        onDone(t("holding.notFound"));
        gone();
      }
    },
  });
  const failed = remove.error instanceof ApiError && remove.error.code === "NOT_FOUND" ? null : remove.error;

  return (
    <Dialog danger title={t("assetEdit.deleteTitle", { name: asset.name })} onClose={onClose}>
      {prices.isPending ? (
        <Loading />
      ) : prices.isError ? (
        <LoadError error={prices.error} onRetry={() => void prices.refetch()} level={3} />
      ) : (
        <p>{t("assetEdit.deleteBody", { count: prices.data.length })}</p>
      )}
      <FormError error={failed} />
      <div className="actions">
        <button type="button" className="button" onClick={onClose}>
          {t("common.cancel")}
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={!prices.isSuccess || remove.isPending}
          onClick={() => {
            if (remove.isPending || deleting.current) return;
            deleting.current = true;
            remove.mutate();
          }}
        >
          {t("assetEdit.delete")}
        </button>
      </div>
    </Dialog>
  );
}
