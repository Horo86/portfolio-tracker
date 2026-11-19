import type { ReactNode } from "react";

export type NavIconName = "dashboard" | "holdings" | "transactions" | "import" | "more";

// Line icons on a 24 grid, drawn with the text's stroke, as StateCard's; decorative only.
const ICONS: Record<NavIconName, ReactNode> = {
  dashboard: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </>
  ),
  holdings: <path d="M4 4v16h16M7 15l4-4 3 3 5-6" />,
  transactions: <path d="M4 8h15l-3-3M20 16H5l3 3" />,
  import: <path d="M12 4v11m-4.5-4.5L12 15l4.5-4.5M5 19h14" />,
  more: <path d="M4 7h16M4 12h16M4 17h16" />,
};

export function NavIcon({ name }: { name: NavIconName }) {
  return (
    <svg
      className="nav-icon"
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {ICONS[name]}
    </svg>
  );
}
