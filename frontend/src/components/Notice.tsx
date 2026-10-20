import { type Ref, useCallback, useState } from "react";

type State = { text: string; serial: number };

/** A confirmation to announce; the same text twice is announced twice. */
export function useNotice() {
  const [notice, setNotice] = useState<State>({ text: "", serial: 0 });
  const announce = useCallback(
    (text: string) => setNotice(({ serial }) => ({ text, serial: serial + 1 })),
    [],
  );
  return { notice, announce };
}

/**
 * The live region stays in the page; each message is a new element inside it, so a screen
 * reader reads it even when the text did not change (e.g. "Settings saved." again).
 */
export function Notice({ notice, ref }: { notice: State; ref?: Ref<HTMLDivElement> }) {
  return (
    <div role="status" ref={ref} tabIndex={-1} className={notice.text ? "notice" : undefined}>
      {notice.text && <span key={notice.serial}>{notice.text}</span>}
    </div>
  );
}
