// The pages of the main menu (mockups 13, 14). Amministrazione: administrators only (D-165).
export type NavItem = { to: string; key: string; end: boolean };

export const NAV: readonly NavItem[] = [
  { to: "/", key: "nav.dashboard", end: true },
  { to: "/accounts", key: "nav.accounts", end: false },
  { to: "/holdings", key: "nav.holdings", end: false },
  { to: "/transactions", key: "nav.transactions", end: false },
  { to: "/import", key: "nav.import", end: false },
  { to: "/settings", key: "nav.settings", end: false },
];
export const ADMIN_ITEM: NavItem = { to: "/admin", key: "nav.admin", end: false };
export const ADMIN_NAV: readonly NavItem[] = [...NAV, ADMIN_ITEM];

// The compact shell (D-189): four pages in the bottom bar, the rest in "Altro".
export const BAR_PATHS = ["/", "/holdings", "/transactions", "/import"] as const;
export const MORE_PATHS = ["/accounts", "/settings", "/admin", "/about"] as const;
