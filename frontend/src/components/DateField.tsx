import { type Ref, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { dayToInput, parseDayInput } from "../format/dateInput";

export type DateFieldProps = {
  label: string;
  /** YYYY-MM-DD, "" when empty, or the text typed while it is not a day. */
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  /** The page's own error (required, not after today…): shown when the day typed reads. */
  error?: string;
  min?: string; // YYYY-MM-DD, for the calendar
  max?: string;
  readOnly?: boolean;
  inputRef?: Ref<HTMLInputElement>;
};

/** What a text hands out: the day it reads, nothing, or the text itself while it is not a
 * day, which every page's own check refuses. */
function handedOut(text: string): string {
  const read = parseDayInput(text);
  return read.kind === "ok" ? read.value : read.kind === "empty" ? "" : text;
}

function setRef<T>(ref: Ref<T> | undefined, node: T | null) {
  if (typeof ref === "function") ref(node);
  else if (ref) ref.current = node;
}

/**
 * A day typed in the app's language, gg/mm/aaaa or dd/mm/yyyy (M5 slice 6), whatever the
 * browser's language: the browser's own date field follows the browser, not the app. A
 * calendar button opens the browser's picker through a hidden date field.
 */
export function DateField({
  label,
  value,
  onChange,
  hint,
  error,
  min,
  max,
  readOnly,
  inputRef,
}: DateFieldProps) {
  const { t } = useTranslation();
  const id = useId();
  const field = useRef<HTMLInputElement | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(() => dayToInput(value));
  const [received, setReceived] = useState(value);
  const [left, setLeft] = useState(false); // told once the field is left, not while typing
  // A value from outside (today's date, the day being edited, filters cleared) replaces
  // what was typed; the value this field handed out itself does not.
  if (value !== received) {
    setReceived(value);
    if (value !== handedOut(text)) {
      setText(dayToInput(value));
      setLeft(false);
    }
  }

  const read = parseDayInput(text);
  let shown = error;
  if (read.kind === "error") {
    const own = read.problem === "format" ? t("dateField.invalidFormat") : t("dateField.impossible");
    shown = left || error ? own : undefined;
  }
  const formatId = `${id}-format`;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = shown ? `${id}-error` : undefined;

  const type = (next: string) => {
    setText(next);
    setLeft(false);
    onChange(handedOut(next));
  };
  const openCalendar = () => {
    const calendar = picker.current;
    if (calendar && typeof calendar.showPicker === "function") {
      try {
        calendar.showPicker();
        return;
      } catch {
        // not allowed here (for example without a user's click): typing still works
      }
    }
    field.current?.focus();
  };

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="date-input">
        <input
          id={id}
          ref={(node) => {
            field.current = node;
            setRef(inputRef, node);
          }}
          type="text"
          inputMode="numeric"
          autoComplete="off"
          spellCheck={false}
          placeholder={t("dateField.placeholder")}
          value={text}
          readOnly={readOnly}
          onChange={(event) => type(event.target.value)}
          onBlur={() => setLeft(true)}
          aria-invalid={shown ? true : undefined}
          aria-describedby={[formatId, hintId, errorId].filter(Boolean).join(" ")}
        />
        {!readOnly && (
          <>
            <button
              type="button"
              className="button date-input-calendar"
              aria-label={t("dateField.calendar", { label })}
              onClick={openCalendar}
            >
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
                <path
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  d="M4 6h16v14H4zM4 10h16M8 3v4M16 3v4"
                />
              </svg>
            </button>
            <input
              ref={picker}
              type="date"
              className="date-input-picker"
              tabIndex={-1}
              aria-hidden="true"
              value={read.kind === "ok" ? read.value : ""}
              min={min}
              max={max}
              onChange={(event) => {
                if (event.target.value) type(dayToInput(event.target.value));
              }}
            />
          </>
        )}
      </div>
      <span id={formatId} className="visually-hidden">
        {t("dateField.format")}
      </span>
      {hint && (
        <span id={hintId} className="field-hint">
          {hint}
        </span>
      )}
      <span aria-live="polite">
        {shown && (
          <span id={errorId} className="field-error">
            {shown}
          </span>
        )}
      </span>
    </div>
  );
}
