import { type Ref, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { dayToInput, notADay, parseDayInput } from "../format/dateInput";

export type DateFieldProps = {
  label: string;
  /** YYYY-MM-DD, "" when empty, or `notADay(text)` while the text typed is not a day. */
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  /** The page's own error (required, not after today…): shown when the day typed reads. */
  error?: string;
  min?: string; // YYYY-MM-DD, for the calendar
  max?: string;
  readOnly?: boolean;
  /** While the page saves: read-only for that moment, so what is saved is what is shown. */
  busy?: boolean;
  inputRef?: Ref<HTMLInputElement>;
};

/** What a text hands out: the day it reads, nothing, or the text marked as not a day, which
 * every page's own check refuses. */
function handedOut(text: string): string {
  const read = parseDayInput(text);
  return read.kind === "ok" ? read.value : read.kind === "empty" ? "" : notADay(text);
}

/** Whether this browser can open its own calendar from a script (showPicker); without it the
 * calendar button would do nothing, so the field is text only. */
function canOpenCalendar(): boolean {
  return typeof HTMLInputElement.prototype.showPicker === "function";
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
  busy,
  inputRef,
}: DateFieldProps) {
  const { t } = useTranslation();
  const id = useId();
  const field = useRef<HTMLInputElement | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(() => dayToInput(value));
  const [received, setReceived] = useState(value);
  // The values handed out since the page last gave a new one: a page may apply them late (the
  // filters' URL changes at a lower priority than typing), and such an echo is not news.
  const [sent, setSent] = useState<string[]>([]);
  const [left, setLeft] = useState(false); // told once the field is left, not while typing
  // A value from outside (today's date, the day being edited, filters cleared) replaces
  // what was typed; a value this field handed out itself does not.
  if (value !== received) {
    setReceived(value);
    setSent([]);
    if (value !== handedOut(text) && !sent.includes(value)) {
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
    if (readOnly || busy) return; // a change that reaches a read-only field anyway
    const out = handedOut(next);
    setText(next);
    setLeft(false);
    setSent((values) => [...values, out]);
    onChange(out);
  };
  const openCalendar = () => {
    try {
      picker.current?.showPicker();
    } catch {
      field.current?.focus(); // not allowed here: typing still works
    }
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
          readOnly={readOnly || busy}
          onChange={(event) => type(event.target.value)}
          onBlur={() => setLeft(true)}
          aria-invalid={shown ? true : undefined}
          aria-describedby={[formatId, hintId, errorId].filter(Boolean).join(" ")}
        />
        {!readOnly && canOpenCalendar() && (
          <>
            <button
              type="button"
              className="button date-input-calendar"
              aria-label={t("dateField.calendar", { label })}
              disabled={busy}
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
