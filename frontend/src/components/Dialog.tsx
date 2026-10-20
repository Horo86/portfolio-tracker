import { type ReactNode, useId, useLayoutEffect, useRef } from "react";

type Props = {
  title: string;
  onClose: () => void;
  children: ReactNode;
  danger?: boolean; // a red band on top, for what cannot be undone (mockup 15)
};

/**
 * A modal dialog (mockup 15), mounted only while open. It is the native <dialog>: the browser
 * keeps the focus inside, closes it with Escape and makes the page behind it inert. On close
 * the focus goes back to the control that opened it.
 */
export function Dialog({ title, onClose, children, danger }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  // Layout effect: its cleanup runs before React removes the element, so it closes in place.
  useLayoutEffect(() => {
    const dialog = ref.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className={danger ? "dialog dialog-danger" : "dialog"}
      aria-labelledby={titleId}
      onCancel={(event) => {
        // Escape: React decides when the dialog goes away.
        event.preventDefault();
        onClose();
      }}
    >
      <h2 id={titleId}>{title}</h2>
      {children}
    </dialog>
  );
}
