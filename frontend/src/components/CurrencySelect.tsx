import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { SelectField } from "./Field";

/** "EUR – Euro" in the interface language; the code alone when the browser has no name. */
export function currencyLabel(code: string, locale: string): string {
  try {
    const name = new Intl.DisplayNames([locale], { type: "currency" }).of(code);
    return name && name !== code ? `${code} – ${name}` : code;
  } catch {
    return code;
  }
}

type Props = {
  label: string;
  value: string;
  onChange: (code: string) => void;
  hint?: string;
  disabled?: boolean;
};

/** The ISO 4217 codes the API accepts (GET /currencies), named in the user's language. */
export function CurrencySelect({ label, value, onChange, hint, disabled }: Props) {
  const { i18n } = useTranslation();
  const currencies = useQuery({
    queryKey: ["currencies"],
    queryFn: () => api<string[]>("GET", "/currencies"),
    staleTime: Infinity,
  });
  const locale = i18n.language;
  // The current value stays selectable while the list loads (or if it fails to).
  const options = useMemo(
    () =>
      (currencies.data ?? [value]).map((code) => ({ code, label: currencyLabel(code, locale) })),
    [currencies.data, value, locale],
  );
  return (
    <SelectField
      label={label}
      hint={hint}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
    >
      {options.map((option) => (
        <option key={option.code} value={option.code}>
          {option.label}
        </option>
      ))}
    </SelectField>
  );
}
