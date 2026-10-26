import type { Ref } from "react";

import { Field } from "./Field";

export type DecimalFieldProps = {
  label: string;
  hint?: string;
  value: string;
  error?: string;
  onChange: (value: string) => void;
  onBlur: () => void;
  hideLabel?: boolean; // read, not shown: a table's column header already shows it
  inputRef?: Ref<HTMLInputElement>;
};

/** A number typed as text: read per language (D-140), never through a binary float. */
export function DecimalField({
  label,
  hint,
  value,
  error,
  onChange,
  onBlur,
  hideLabel,
  inputRef,
}: DecimalFieldProps) {
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
      hideLabel={hideLabel}
      inputRef={inputRef}
    />
  );
}
