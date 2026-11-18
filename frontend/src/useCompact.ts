import { useSyncExternalStore } from "react";

/**
 * The compact shell's breakpoint (D-189): below 1024 px the sidebar becomes a top strip and a
 * bar at the bottom. `styles/responsive.css` uses the same query, kept equal by a unit test.
 * The one place where script reads the breakpoint: it picks which navigation is in the page,
 * so there is never a second, hidden copy of the menu. Everything else is CSS only.
 */
export const COMPACT_QUERY = "(max-width: 1023.98px)";

function query(): MediaQueryList | undefined {
  try {
    return window.matchMedia?.(COMPACT_QUERY) ?? undefined;
  } catch {
    return undefined;
  }
}

function subscribe(listener: () => void): () => void {
  const list = query();
  if (!list?.addEventListener) return () => {};
  list.addEventListener("change", listener);
  return () => list.removeEventListener("change", listener);
}

// A browser without matchMedia (and jsdom) gets the desktop shell.
const compactNow = () => query()?.matches ?? false;

/** Whether the window is narrower than 1024 px, followed live. */
export function useCompact(): boolean {
  return useSyncExternalStore(subscribe, compactNow, () => false);
}
