import { type Ref, useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";

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

/** The confirmation a form left in the navigation state (`notice`), announced once: the
 * state is cleared, so a reload or the back button does not repeat it. */
export function useSavedNotice() {
  const location = useLocation();
  const navigate = useNavigate();
  const { notice, announce } = useNotice();
  const text = (location.state as { notice?: unknown } | null)?.notice;
  useEffect(() => {
    if (typeof text !== "string") return;
    announce(text);
    const here = { pathname: location.pathname, search: location.search };
    navigate(here, { replace: true, state: null });
  }, [text, announce, navigate, location.pathname, location.search]);
  return { notice, announce };
}
