import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { type ReactNode, useState } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type {
  Account,
  AdminUser,
  Asset,
  ManualPrice,
  Me,
  PortfolioPositions,
  QuantityConflict,
  Transaction,
} from "../api/types";
import i18n from "../i18n";
import { AccountFormDialog, DeleteAccountDialog } from "../pages/AccountDialogs";
import { BulkDeleteDialog, BulkEditDialog, DeleteAllDialog } from "../pages/TransactionBulk";
import { ConflictDialog, DeleteTransactionDialog } from "../pages/TransactionDialogs";
import { type Handler, stubFetch } from "../test/fetchStub";
import { portfolioRow, WORLD_ETF } from "../test/holdings";
import { stubPriceSources } from "../test/priceSources";
import { HEALTH, ME, NO_ACCOUNTS, renderApp } from "../test/renderApp";
import { AssetEditDialog, DeleteAssetDialog } from "./AssetEditDialog";
import MoreMenu from "./MoreMenu";
import { ReauthDialog } from "./ReauthDialog";

// A phone has no Escape key (D-190): every dialog of the app shows a button that closes it.
// Each place that opens a `Dialog` has a case below that opens it and closes it with such a
// button; a new place fails the inventory until it gets one. Invented data only.

const t = (key: string) => i18n.t(key);

// --- Invented data -----------------------------------------------------------------------

const EUR: Account = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Conto Esempio",
  provider: null,
  currency: "EUR",
  external_code: null,
  created_at: "2026-01-10T09:00:00Z",
  transaction_count: 2,
  currency_locked: false,
};

const ETF: Asset = {
  id: "00000000-0000-4000-8000-0000000000b1",
  isin: null,
  ticker: null,
  name: "Mondo Azionario ETF",
  asset_type: "ETF",
  quote_currency: "EUR",
  quote_unit: "CURRENCY",
  shared: false,
  in_use: false,
  created_at: "2026-01-10T09:00:00Z",
};

const TX: Transaction = {
  id: "00000000-0000-4000-8000-0000000000c1",
  sequence: 1,
  account_id: EUR.id,
  type: "BUY",
  trade_date: "2026-10-01",
  trade_time: null,
  value_date: null,
  asset_id: ETF.id,
  quantity: "10",
  unit_price: "100",
  gross_amount: "1000",
  fees: "0",
  accrued_interest_paid: null,
  accrued_interest_received: null,
  taxes: "0",
  total_amount: "1000",
  split_ratio: null,
  affects_cash: true,
  external_ref: null,
  notes: null,
  metadata: null,
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
};

const CONFLICT: QuantityConflict = {
  reason: "NEGATIVE_QUANTITY",
  account_id: EUR.id,
  asset_id: ETF.id,
  quantity: "-1",
  held: "0",
};

const OCT_1: ManualPrice = {
  id: "00000000-0000-4000-8000-000000000101",
  day: "2026-10-01",
  close: "30.8",
  source: "MANUAL",
};

const POSITIONS: PortfolioPositions = {
  currency: "EUR",
  day: "2026-10-24",
  partial: false,
  unavailable_currencies: [],
  stale_fx: [],
  positions: [portfolioRow(WORLD_ETF)],
};

const ADMIN = { ...ME, role: "ADMINISTRATOR" } as const;
const MARTA: AdminUser = {
  id: "00000000-0000-4000-8000-000000000002",
  username: "marta",
  first_name: "Marta",
  last_name: "Esempio",
  email: "marta@example.org",
  role: "INVESTOR",
  two_factor_enabled: false,
  must_change_password: false,
  disabled: false,
  created_at: "2026-10-01T09:00:00Z",
  last_login_at: null,
};
const SELF: AdminUser = { ...MARTA, id: ADMIN.id, username: "mario", role: "ADMINISTRATOR" };

const NO_LICENCES = {
  "GET /licenses/frontend.json": { status: 404 },
  "GET /licenses/backend.json": { status: 404 },
};
const SESSIONS = {
  body: [
    {
      id: "00000000-0000-4000-8000-000000000501",
      created_at: "2026-10-18T12:00:00Z",
      last_used_at: "2026-10-20T12:00:00Z",
      expires_at: "2026-11-17T12:00:00Z",
      user_agent: null,
      current: true,
    },
  ],
};
const RECOVERY = Array.from({ length: 10 }, (_, i) => `Ab${i}c-Def${i}-Ghj${i}-Kmn${i}`);

// --- Helpers -----------------------------------------------------------------------------

/** A component dialog, open until its `onClose` is called, as its page holds it. */
function Opened({ dialog }: { dialog: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(true);
  return open ? <>{dialog(() => setOpen(false))}</> : null;
}

function mount(dialog: (close: () => void) => ReactNode, routes: Record<string, Handler> = {}) {
  stubFetch({ "GET /api/health": HEALTH, ...routes });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Opened dialog={dialog} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const noop = () => {};
const click = (name: string | RegExp, scope: HTMLElement = document.body) =>
  fireEvent.click(within(scope).getByRole("button", { name }));
const change = (scope: HTMLElement, label: string, value: string) =>
  fireEvent.change(within(scope).getByLabelText(label), { target: { value } });

/** Shown on screen: not hidden, not inside something hidden or read-only for readers. */
function shown(element: HTMLElement): boolean {
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    if (node.hidden || node.getAttribute("aria-hidden") === "true") return false;
    if (node.classList.contains("visually-hidden")) return false;
  }
  return true;
}

type Case = {
  file: string; // the source file whose <Dialog> this is
  open: () => Promise<void>; // renders and opens it
  dialog: string | RegExp; // its accessible name
  close: string; // the visible button that closes it
};

// --- The cases, one per <Dialog> in the app ----------------------------------------------

const CASES: Record<string, Case> = {
  "edit a security": {
    file: "components/AssetEditDialog.tsx",
    dialog: "Edit security",
    close: "common.cancel",
    open: async () =>
      mount(
        (close) => (
          <AssetEditDialog asset={ETF} marketData="OFF" onClose={close} onSaved={noop} onDelete={noop} />
        ),
        { "GET /api/v1/currencies": { body: ["EUR", "USD"] } },
      ),
  },
  "delete a security": {
    file: "components/AssetEditDialog.tsx",
    dialog: /^Delete/,
    close: "common.cancel",
    open: async () =>
      mount((close) => <DeleteAssetDialog asset={ETF} onClose={close} onDone={noop} />, {
        [`GET /api/v1/assets/${ETF.id}/prices`]: { body: [] },
      }),
  },
  "ask the password again": {
    file: "components/ReauthDialog.tsx",
    dialog: "Confirm",
    close: "common.cancel",
    open: async () =>
      mount((close) => (
        <ReauthDialog
          heading="Confirm"
          submit="Go on"
          path="/me/example"
          withCode
          passwordLabel="Password"
          codeLabel="Code"
          codeHint="A code"
          onClose={close}
          onDone={noop}
        />
      )),
  },
  "turn on two-step verification": {
    file: "components/TwoFactorSettings.tsx",
    dialog: /two-step verification/i,
    close: "common.cancel",
    open: async () => {
      stubFetch(settingsRoutes({}));
      renderApp("/settings");
      await screen.findByRole("heading", { name: "Settings", level: 1 });
      click(/^Turn on/);
    },
  },
  "the recovery codes, shown once": {
    file: "components/TwoFactorSettings.tsx",
    dialog: "Recovery codes",
    close: "twoFactor.codes.saved",
    open: async () => {
      stubFetch(
        settingsRoutes(
          { two_factor_enabled: true, recovery_codes_left: 7 },
          {
            "POST /api/v1/me/two-factor/recovery-codes": {
              body: { user: { ...ME, two_factor_enabled: true }, token: null, recovery_codes: RECOVERY },
            },
          },
        ),
      );
      renderApp("/settings");
      await screen.findByRole("heading", { name: "Settings", level: 1 });
      click("New recovery codes");
      const ask = await screen.findByRole("dialog", { name: "New recovery codes?" });
      change(ask, "Password", "an invented password");
      change(ask, "Code", "Abcd-Efgh-Jkmn-Pqrs");
      click("Create codes", ask);
    },
  },
  "the compact shell's menu": {
    file: "components/MoreMenu.tsx",
    dialog: "More",
    close: "nav.close",
    open: async () => mount((close) => <MoreMenu me={ME as Me} search="" onClose={close} />),
  },
  "a new security from the transaction form": {
    file: "pages/TransactionForm.tsx",
    dialog: "New security",
    close: "common.cancel",
    open: async () => {
      stubFetch({
        "GET /api/v1/me": { body: ME },
        "GET /api/health": HEALTH,
        "GET /api/v1/accounts": { body: [EUR] },
        "GET /api/v1/assets": { body: [ETF] },
        "GET /api/v1/currencies": { body: ["EUR", "USD"] },
      });
      renderApp("/transactions/new");
      await screen.findByRole("heading", { name: "New transaction", level: 1 });
      click("New security…");
    },
  },
  "edit several transactions": {
    file: "pages/TransactionBulk.tsx",
    dialog: /^Edit 1 transaction/,
    close: "common.cancel",
    open: async () =>
      mount((close) => (
        <BulkEditDialog txs={[TX]} accounts={[EUR]} onClose={close} onSaved={noop} onConflict={noop} />
      )),
  },
  "delete several transactions": {
    file: "pages/TransactionBulk.tsx",
    dialog: /^Delete 1 transaction/,
    close: "common.cancel",
    open: async () =>
      mount((close) => (
        <BulkDeleteDialog ids={[TX.id]} onClose={close} onDeleted={noop} onConflict={noop} />
      )),
  },
  "delete all of a security's transactions": {
    file: "pages/TransactionBulk.tsx",
    dialog: /^Delete all/,
    close: "common.cancel",
    open: async () =>
      mount((close) => (
        <DeleteAllDialog account={EUR} asset={ETF} count={2} onClose={close} onDeleted={noop} />
      )),
  },
  "a new security from add holdings I already own": {
    file: "pages/AddExisting.tsx",
    dialog: "Add a security",
    close: "common.cancel",
    open: async () => {
      stubFetch({
        "GET /api/v1/me": { body: ME },
        "GET /api/health": HEALTH,
        "GET /api/v1/accounts": { body: [EUR] },
        "GET /api/v1/assets": { body: [ETF] },
        "GET /api/v1/currencies": { body: ["EUR", "USD"] },
        [`GET /api/v1/accounts/${EUR.id}/positions?include_closed=true`]: { body: [] },
      });
      renderApp(`/holdings/existing?account=${EUR.id}`);
      await screen.findByRole("heading", { name: "Add holdings I already own", level: 1 });
      click("New security for row 1");
    },
  },
  "delete a manual price": {
    file: "pages/ManualPrices.tsx",
    dialog: "Delete the price of Oct 1, 2026?",
    close: "common.cancel",
    open: async () => {
      stubFetch({
        "GET /api/v1/me": { body: ME },
        "GET /api/health": HEALTH,
        "GET /api/v1/accounts": NO_ACCOUNTS,
        "GET /api/v1/assets": { body: [ETF] },
        "GET /api/v1/portfolio/positions?include_closed=true": { body: POSITIONS },
        [`GET /api/v1/assets/${ETF.id}/prices`]: { body: [OCT_1] },
      });
      renderApp(`/holdings/${ETF.id}/prices`);
      fireEvent.click(await screen.findByRole("button", { name: "Delete the price of Oct 1, 2026" }));
    },
  },
  "a new user": {
    file: "pages/Administration.tsx",
    dialog: "New user",
    close: "common.cancel",
    open: async () => {
      await adminPage();
      click("New user");
    },
  },
  "disable a user": {
    file: "pages/Administration.tsx",
    dialog: /^Disable marta/,
    close: "common.cancel",
    open: async () => {
      await adminPage();
      click("Disable marta");
    },
  },
  "the temporary password, shown once": {
    file: "pages/Administration.tsx",
    dialog: "User created",
    close: "admin.close",
    open: async () => {
      await adminPage({
        "POST /api/v1/admin/users": {
          status: 201,
          body: { user: { ...MARTA, id: "new", username: "giulia" }, temporary_password: "Wd7n-Qa3L-xP58" },
        },
      });
      click("New user");
      const form = screen.getByRole("dialog", { name: "New user" });
      change(form, "Username", "giulia");
      change(form, "First name", "Giulia");
      change(form, "Last name", "Esempio");
      change(form, "E-mail", "giulia@example.org");
      click("Create user", form);
    },
  },
  "a refused change": {
    file: "pages/TransactionDialogs.tsx",
    dialog: /can’t be saved|cannot be saved/,
    close: "conflict.back",
    open: async () =>
      mount((close) => <ConflictDialog conflict={CONFLICT} asset={ETF} onClose={close} />),
  },
  "delete a transaction": {
    file: "pages/TransactionDialogs.tsx",
    dialog: /^Delete this transaction/,
    close: "common.cancel",
    open: async () =>
      mount(
        (close) => (
          <DeleteTransactionDialog
            tx={TX}
            asset={ETF}
            account={EUR}
            onClose={close}
            onDeleted={noop}
            onConflict={noop}
          />
        ),
        {
          "GET /api/v1/accounts": { body: [EUR] },
          // The dry run is not what this test is about: it fails, the dialog stays.
          "POST /api/v1/transactions/preview": { status: 503, body: { code: "X", detail: "x" } },
        },
      ),
  },
  "leave a role without a source": {
    file: "pages/PriceSources.tsx",
    dialog: "Leave without a source?",
    close: "common.cancel",
    open: async () => {
      stubPriceSources();
      renderApp("/admin/price-sources");
      fireEvent.click(await screen.findByRole("button", { name: "Remove Frankfurter" }));
    },
  },
  "change the password": {
    file: "pages/Settings.tsx",
    dialog: "Change password",
    close: "common.cancel",
    open: async () => {
      stubFetch(settingsRoutes({}));
      renderApp("/settings");
      await screen.findByRole("heading", { name: "Settings", level: 1 });
      click("Change password");
    },
  },
  "the release notes": {
    file: "pages/About.tsx",
    dialog: "Release notes",
    close: "about.releaseNotesClose",
    open: async () => {
      stubFetch({
        "GET /api/v1/me": { body: ME },
        "GET /api/health": HEALTH,
        "GET /api/v1/accounts": NO_ACCOUNTS,
        "GET /notes/release-notes.json": { status: 404 },
        ...NO_LICENCES,
      });
      renderApp("/about");
      fireEvent.click(
        await screen.findByRole("button", { name: `${HEALTH.body.version} Release notes` }),
      );
    },
  },
  "a new account": {
    file: "pages/AccountDialogs.tsx",
    dialog: "New account",
    close: "common.cancel",
    open: async () =>
      mount(
        (close) => <AccountFormDialog defaultCurrency="EUR" onClose={close} onSaved={noop} />,
        { "GET /api/v1/currencies": { body: ["EUR", "USD"] } },
      ),
  },
  "delete an account": {
    file: "pages/AccountDialogs.tsx",
    dialog: /^Delete the account/,
    close: "common.cancel",
    open: async () =>
      mount((close) => <DeleteAccountDialog account={EUR} onClose={close} onDeleted={noop} />),
  },
};

function settingsRoutes(me: object, extra: Record<string, Handler> = {}): Record<string, Handler> {
  return {
    "GET /api/v1/me": { body: { ...ME, ...me } },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": NO_ACCOUNTS,
    "GET /api/v1/currencies": { body: ["EUR"] },
    "GET /api/v1/me/sessions": SESSIONS,
    ...NO_LICENCES,
    ...extra,
  };
}

async function adminPage(extra: Record<string, Handler> = {}) {
  stubFetch({
    "GET /api/v1/me": { body: ADMIN },
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": NO_ACCOUNTS,
    "GET /api/v1/admin/users": { body: [SELF, MARTA] },
    "GET /api/v1/admin/market-data/status": {
      body: { market_data: "OFF", engines: [], routes: {} },
    },
    "GET /api/v1/admin/security-settings": { status: 503, body: { code: "X", detail: "x" } },
    ...extra,
  });
  renderApp("/admin");
  await screen.findByRole("rowheader", { name: /^marta\b/ });
}

// --- The inventory and the checks --------------------------------------------------------

const SOURCES = import.meta.glob<string>(["../**/*.tsx", "!../**/*.test.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
});

describe("every dialog closes with a visible button (D-190: a phone has no Escape)", () => {
  it("has a case for each <Dialog> in the app, and none for one that is gone", () => {
    const found: Record<string, number> = {};
    for (const [path, text] of Object.entries(SOURCES)) {
      const count = (text.match(/<Dialog[\s>]/g) ?? []).length;
      // Keys are relative to this folder: "./X.tsx" here, "../pages/X.tsx" elsewhere.
      const file = path.startsWith("./") ? `components/${path.slice(2)}` : path.slice(3);
      if (count > 0 && file !== "components/Dialog.tsx") found[file] = count;
    }
    const covered: Record<string, number> = {};
    for (const { file } of Object.values(CASES)) covered[file] = (covered[file] ?? 0) + 1;
    expect(covered).toEqual(found);
    expect(Object.keys(CASES)).toHaveLength(22);
  });

  for (const [name, { open, dialog, close }] of Object.entries(CASES)) {
    it(name, async () => {
      await open();
      const shownDialog = await screen.findByRole("dialog", { name: dialog });
      const label = close.includes(".") ? t(close) : close;
      const button = within(shownDialog).getByRole("button", { name: label });
      expect(shown(button), `"${label}" is shown`).toBe(true);
      expect(button).toBeEnabled();
      fireEvent.click(button);
      await waitFor(() =>
        expect(screen.queryByRole("dialog", { name: dialog })).not.toBeInTheDocument(),
      );
    });
  }
});
