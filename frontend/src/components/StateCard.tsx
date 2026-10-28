import type { ReactNode } from "react";

import { PageTitle } from "./PageTitle";

export type StateIcon =
  | "account"
  | "holdings"
  | "transactions"
  | "search"
  | "file"
  | "error"
  | "notFound";

// Line icons on a 24 grid, drawn with the text's stroke; decorative only.
const ICONS: Record<StateIcon, ReactNode> = {
  account: (
    <>
      <path d="M3 10 12 4l9 6" />
      <path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18" />
    </>
  ),
  holdings: <path d="M4 4v16h16M7 15l4-4 3 3 5-6" />,
  transactions: <path d="M4 8h15l-3-3M20 16H5l3 3" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="m20 20-4.5-4.5" />
    </>
  ),
  file: (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M10 12l4 4m0-4-4 4" />
    </>
  ),
  error: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5M12 16h.01" />
    </>
  ),
  notFound: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m9 9 6 6m0-6-6 6" />
    </>
  ),
};

type Props = {
  icon: StateIcon;
  // None when the page's own heading says it (the dashboard's greeting, D-153).
  heading?: string;
  // The heading fits the page's order: 1 is the page's title, 3 inside a card or a dialog.
  level?: 1 | 2 | 3;
  role?: "alert";
  children?: ReactNode;
  actions?: ReactNode;
};

/** An empty or error state (mockup 19): icon tile, heading, text and actions. */
export function StateCard({ icon, heading, level = 2, role, children, actions }: Props) {
  const Heading = level === 3 ? "h3" : "h2";
  return (
    <section className="card state-card" role={role}>
      <span className="state-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="20" height="20" focusable="false">
          {ICONS[icon]}
        </svg>
      </span>
      {heading && (level === 1 ? <PageTitle title={heading} /> : <Heading>{heading}</Heading>)}
      {children}
      {actions && <div className="actions">{actions}</div>}
    </section>
  );
}
