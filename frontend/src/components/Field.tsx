import {
  type InputHTMLAttributes,
  type ReactNode,
  type Ref,
  type SelectHTMLAttributes,
  useId,
} from "react";

type Described = { label: string; hint?: string; error?: string };

/** The ids of a field and of its hint and error, announced with it (aria-describedby). */
function useDescribed(hint?: string, error?: string) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return { id, hintId, errorId, describedBy };
}

type NotesProps = { hint?: string; error?: string; hintId?: string; errorId?: string };

function Notes({ hint, error, hintId, errorId }: NotesProps) {
  return (
    <>
      {hint && (
        <span id={hintId} className="field-hint">
          {hint}
        </span>
      )}
      {error && (
        <span id={errorId} className="field-error">
          {error}
        </span>
      )}
    </>
  );
}

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "id"> &
  Described & { inputRef?: Ref<HTMLInputElement>; hideLabel?: boolean };

/** A labelled input; its hint and error are announced with it. `hideLabel`: the label is
 * read, not shown (a table's column header already shows it). */
export function Field({ label, hint, error, inputRef, hideLabel, ...input }: Props) {
  const { id, hintId, errorId, describedBy } = useDescribed(hint, error);
  return (
    <div className="field">
      <label htmlFor={id} className={hideLabel ? "visually-hidden" : undefined}>
        {label}
      </label>
      <input
        id={id}
        ref={inputRef}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        {...input}
      />
      <Notes hint={hint} error={error} hintId={hintId} errorId={errorId} />
    </div>
  );
}

type SelectProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> &
  Described & { children: ReactNode; hideLabel?: boolean; selectRef?: Ref<HTMLSelectElement> };

/** A labelled select, like Field. `hideLabel`: the label is read, not shown (a settings row
 * already shows it). */
export function SelectField({
  label,
  hint,
  error,
  children,
  hideLabel,
  selectRef,
  ...select
}: SelectProps) {
  const { id, hintId, errorId, describedBy } = useDescribed(hint, error);
  return (
    <div className="field">
      <label htmlFor={id} className={hideLabel ? "visually-hidden" : undefined}>
        {label}
      </label>
      <select
        id={id}
        ref={selectRef}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        {...select}
      >
        {children}
      </select>
      <Notes hint={hint} error={error} hintId={hintId} errorId={errorId} />
    </div>
  );
}
