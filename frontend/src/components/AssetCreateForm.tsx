import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import type { Asset, AssetType, QuoteUnit } from "../api/types";
import { issueText } from "../errors";
import { ASSETS_KEY, useAssets } from "../transactions";
import { isinProblem, normalIsin } from "../validation";
import { CurrencySelect } from "./CurrencySelect";
import { Field, SelectField } from "./Field";
import { FormError } from "./FormError";

const ASSET_TYPES: readonly AssetType[] = ["ETF", "STOCK", "BOND"];

/** The price units a type can have: bonds, and only bonds, as % of nominal (D-67). While
 * no type is chosen, those of the unit given. */
function unitsOf(type: AssetType | null, unit?: QuoteUnit): QuoteUnit[] {
  const bond = type === "BOND" || (type === null && unit === "PERCENT_OF_NOMINAL");
  return bond ? ["PERCENT_OF_NOMINAL"] : ["CURRENCY", "MINOR_CURRENCY_UNIT"];
}

/** Values to start from, e.g. a search result (D-148); `asset_type` null: to be chosen. */
export type AssetDraft = {
  name?: string;
  asset_type?: AssetType | null;
  quote_currency?: string;
  quote_unit?: QuoteUnit;
  isin?: string | null;
  ticker?: string | null;
};

type Props = {
  defaultCurrency: string;
  defaultType?: AssetType;
  initial?: AssetDraft;
  nameHint?: string;
  focusName?: boolean; // the name takes the focus when the form appears
  existing?: (asset: Asset) => ReactNode; // offered for the user's security with this ISIN
  onCreated: (asset: Asset) => void;
  onCancel: () => void;
};

/**
 * Mockup 11, lower half, "Non lo trovi? Crealo a mano": a security created by hand, private
 * to its creator (D-37, D-116). With an ISIN the server looks for automatic prices; the
 * listing is never chosen here (D-125). Used in a dialog from the transaction form (D-141),
 * and under the search of screen 11, where a result fills it in (D-148).
 */
export function AssetCreateForm({
  defaultCurrency,
  defaultType,
  initial = {},
  nameHint,
  focusName = false,
  existing,
  onCreated,
  onCancel,
}: Props) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const startType = initial.asset_type === undefined ? (defaultType ?? "ETF") : initial.asset_type;
  const startUnits = unitsOf(startType, initial.quote_unit);
  const [name, setName] = useState(initial.name ?? "");
  const [type, setType] = useState<AssetType | null>(startType);
  const [currency, setCurrency] = useState(initial.quote_currency ?? defaultCurrency);
  const [unit, setUnit] = useState<QuoteUnit>(
    initial.quote_unit && startUnits.includes(initial.quote_unit) ? initial.quote_unit : startUnits[0],
  );
  const [isin, setIsin] = useState(initial.isin ?? "");
  const [ticker, setTicker] = useState(initial.ticker ?? "");
  const [submitted, setSubmitted] = useState(false);
  const [isinLeft, setIsinLeft] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const typeRef = useRef<HTMLSelectElement>(null);
  const isinRef = useRef<HTMLInputElement>(null);
  // Without a proposed type the choice is the user's (D-148); from scratch it starts on ETF.
  const typeChoice = startType === null;

  useEffect(() => {
    if (focusName) nameRef.current?.focus();
  }, [focusName]);

  const isinText = normalIsin(isin);
  const isinBad = isinText === "" ? null : isinProblem(isinText);
  const nameMissing = name.trim() === "";

  // Set when a save starts, before any render: a second click in the same moment sees it,
  // where the mutation's own state may not have reached this closure yet.
  const saving = useRef(false);
  const create = useMutation({
    onSettled: () => {
      saving.current = false;
    },
    mutationFn: (assetType: AssetType) =>
      api<Asset>("POST", "/assets", {
        name: name.trim(),
        asset_type: assetType,
        quote_currency: currency,
        quote_unit: unit,
        ...(isinText ? { isin: isinText } : {}),
        ...(ticker.trim() ? { ticker: ticker.trim() } : {}),
      }),
    onSuccess: (asset) => {
      client.setQueryData<Asset[]>(ASSETS_KEY, (list) => (list ? [...list, asset] : list));
      void client.invalidateQueries({ queryKey: ASSETS_KEY });
      onCreated(asset);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "ISIN_ALREADY_USED") {
        isinRef.current?.focus();
      }
    },
  });

  const chooseType = (next: AssetType | null) => {
    setType(next);
    if (!unitsOf(next, unit).includes(unit)) setUnit(unitsOf(next, unit)[0]);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (create.isPending || saving.current) return;
    setSubmitted(true);
    if (nameMissing) nameRef.current?.focus();
    else if (type === null) typeRef.current?.focus();
    else if (isinBad) isinRef.current?.focus();
    else {
      saving.current = true;
      create.mutate(type);
    }
  };

  // A refusal of one field is shown at the field; anything else above the form.
  const failed = create.error;
  const isinTaken = failed instanceof ApiError && failed.code === "ISIN_ALREADY_USED";
  const unitIssue =
    failed instanceof ApiError && failed.code === "VALIDATION_FAILED"
      ? (failed.extra.issues as { field: string; code: string }[] | undefined)?.find(
          (issue) => issue.field === "quote_unit",
        )
      : undefined;
  let isinError: string | undefined;
  if (isinBad && (submitted || isinLeft)) isinError = t(isinBad);
  else if (isinTaken) isinError = t("errors.ISIN_ALREADY_USED");
  // The user's own security with that ISIN (D-148), from their list once it is refused.
  const own = useAssets(isinTaken && existing !== undefined);
  const taken = isinTaken ? own.data?.find((a) => a.isin === isinText) : undefined;

  return (
    <form onSubmit={submit} noValidate>
      <FormError error={isinTaken || unitIssue ? null : failed} />
      <Field
        label={t("assetForm.name")}
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={200}
        autoComplete="off"
        hint={nameHint}
        error={submitted && nameMissing ? t("validation.required") : undefined}
        inputRef={nameRef}
        required
      />
      <div className="form-grid">
        <SelectField
          label={t("assetForm.type")}
          value={type ?? ""}
          onChange={(e) => chooseType((e.target.value || null) as AssetType | null)}
          error={submitted && type === null ? t("validation.required") : undefined}
          selectRef={typeRef}
          required={typeChoice}
        >
          {typeChoice && <option value="">{t("assetForm.chooseType")}</option>}
          {ASSET_TYPES.map((value) => (
            <option key={value} value={value}>
              {t(`assetTypes.${value}`)}
            </option>
          ))}
        </SelectField>
        <CurrencySelect label={t("assetForm.currency")} value={currency} onChange={setCurrency} />
      </div>
      <div className="form-grid">
        <Field
          label={t("assetForm.isin")}
          hint={t("assetForm.isinHint")}
          value={isin}
          onChange={(e) => {
            setIsin(e.target.value);
            if (isinTaken) create.reset();
          }}
          onBlur={() => setIsinLeft(true)}
          maxLength={20}
          autoComplete="off"
          spellCheck={false}
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
      {taken && existing && (
        <p className="notice">
          {t("assetSearch.alreadyHave", { name: taken.name })} {existing(taken)}
        </p>
      )}
      <SelectField
        label={t("assetForm.unit")}
        hint={type === "BOND" ? t("assetForm.unitBond") : undefined}
        value={unit}
        onChange={(e) => setUnit(e.target.value as QuoteUnit)}
        error={unitIssue ? issueText(t, unitIssue.code) : undefined}
      >
        {unitsOf(type, unit).map((value) => (
          <option key={value} value={value}>
            {t(`quoteUnits.${value}`, { currency })}
          </option>
        ))}
      </SelectField>
      <div className="actions">
        <button type="button" className="button" onClick={onCancel}>
          {t("common.cancel")}
        </button>
        <button type="submit" className="button button-primary" disabled={create.isPending}>
          {t("assetForm.create")}
        </button>
      </div>
    </form>
  );
}
