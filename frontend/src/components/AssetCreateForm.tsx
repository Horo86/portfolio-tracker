import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import type { Asset, AssetType, QuoteUnit } from "../api/types";
import { issueText } from "../errors";
import { ASSETS_KEY } from "../transactions";
import { isinProblem, normalIsin } from "../validation";
import { CurrencySelect } from "./CurrencySelect";
import { Field, SelectField } from "./Field";
import { FormError } from "./FormError";

const ASSET_TYPES: readonly AssetType[] = ["ETF", "STOCK", "BOND"];

/** The price units a type can have: bonds, and only bonds, as % of nominal (D-67). */
function unitsOf(type: AssetType): QuoteUnit[] {
  return type === "BOND" ? ["PERCENT_OF_NOMINAL"] : ["CURRENCY", "MINOR_CURRENCY_UNIT"];
}

type Props = {
  defaultCurrency: string;
  defaultType?: AssetType;
  onCreated: (asset: Asset) => void;
  onCancel: () => void;
};

/**
 * Mockup 11, lower half, "Non lo trovi? Crealo a mano": a security created by hand, private
 * to its creator (D-37, D-116). With an ISIN the server looks for automatic prices; the
 * listing is never chosen here (D-125). Used in a dialog from the transaction form (D-141),
 * and on the page of screen 11 later.
 */
export function AssetCreateForm({ defaultCurrency, defaultType, onCreated, onCancel }: Props) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [type, setType] = useState<AssetType>(defaultType ?? "ETF");
  const [currency, setCurrency] = useState(defaultCurrency);
  const [unit, setUnit] = useState<QuoteUnit>(unitsOf(defaultType ?? "ETF")[0]);
  const [isin, setIsin] = useState("");
  const [ticker, setTicker] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [isinLeft, setIsinLeft] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const isinRef = useRef<HTMLInputElement>(null);

  const isinText = normalIsin(isin);
  const isinBad = isinText === "" ? null : isinProblem(isinText);
  const nameMissing = name.trim() === "";

  const create = useMutation({
    mutationFn: () =>
      api<Asset>("POST", "/assets", {
        name: name.trim(),
        asset_type: type,
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

  const chooseType = (next: AssetType) => {
    setType(next);
    if (!unitsOf(next).includes(unit)) setUnit(unitsOf(next)[0]);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    if (nameMissing) nameRef.current?.focus();
    else if (isinBad) isinRef.current?.focus();
    else create.mutate();
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

  return (
    <form onSubmit={submit} noValidate>
      <FormError error={isinTaken || unitIssue ? null : failed} />
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
          value={type}
          onChange={(e) => chooseType(e.target.value as AssetType)}
        >
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
      <SelectField
        label={t("assetForm.unit")}
        hint={type === "BOND" ? t("assetForm.unitBond") : undefined}
        value={unit}
        onChange={(e) => setUnit(e.target.value as QuoteUnit)}
        error={unitIssue ? issueText(t, unitIssue.code) : undefined}
      >
        {unitsOf(type).map((value) => (
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
