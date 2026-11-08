import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { stubFetch } from "../test/fetchStub";
import { HEALTH, ME, NO_ACCOUNTS, renderApp } from "../test/renderApp";

const SYSTEM = {
  version: "1.0.0",
  schema_revision: "0006",
  schema_head: "0006",
  last_upgrade: {
    from_revision: "0005",
    to_revision: "0006",
    version: "1.0.0",
    at: "2026-03-01T10:15:00Z",
    status: "done",
    copy_path: "backups/pre-migration/app-0005-20260301T101500.123456Z.sqlite",
  },
  pre_migration_copies: [
    { path: "backups/pre-migration/app-0005-20260301T101500.123456Z.sqlite", size: 20, at: "2026-03-01T10:15:00Z", status: "done" },
    { path: "backups/pre-migration/app-0004-20260101T000000Z.sqlite", size: 10, at: "2026-01-01T00:00:00Z", status: null },
  ],
  latest_backup: { path: "backups/manual/portfolio-1.0.0-20260302T080000Z.zip", size: 6602, at: "2026-03-02T08:00:00Z" },
};

const FRONTEND = [
  { name: "echarts", version: "6.1.0", license: "Apache-2.0" },
  { name: "react", version: "19.2.0", license: "MIT" },
];
const BACKEND = [
  { name: "fastapi", version: "0.143.0", license: "MIT" },
  { name: "only-a-file", version: "1.0", license: null },
  { name: "SQLAlchemy", version: "2.1.4", license: "MIT" },
];
const NO_LISTS = {
  "GET /licenses/frontend.json": { status: 404 },
  "GET /licenses/backend.json": { status: 404 },
};

function common() {
  return {
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": NO_ACCOUNTS,
    "GET /api/v1/setup/status": { body: { setup_required: false } },
    ...NO_LISTS,
  };
}

describe("About, the System section (D-168)", () => {
  it("shows an administrator the schema, the last upgrade with its copy and the latest backup", async () => {
    stubFetch({
      ...common(),
      "GET /api/v1/me": { body: { ...ME, role: "ADMINISTRATOR" } },
      "GET /api/v1/admin/system": { body: SYSTEM },
    });
    renderApp("/about");
    const section = await screen.findByRole("region", { name: "System" });
    expect(await screen.findByText(/From 0005 to 0006, by version 1.0.0/)).toBeInTheDocument();
    expect(section).toHaveTextContent("0006 · Up to date");
    expect(section).toHaveTextContent(
      "Copy taken before it: backups/pre-migration/app-0005-20260301T101500.123456Z.sqlite",
    );
    expect(section).toHaveTextContent("2 kept (the newest three are kept)");
    expect(section).toHaveTextContent("backups/manual/portfolio-1.0.0-20260302T080000Z.zip, 6.4 kB");
    expect(section.textContent).not.toMatch(/\/data\b|\/home\//);
  });

  it("says when there was no upgrade, no copy and no backup", async () => {
    stubFetch({
      ...common(),
      "GET /api/v1/me": { body: { ...ME, role: "ADMINISTRATOR" } },
      "GET /api/v1/admin/system": {
        body: { ...SYSTEM, last_upgrade: null, pre_migration_copies: [], latest_backup: null },
      },
    });
    renderApp("/about");
    expect(await screen.findByText("No upgrade yet")).toBeInTheDocument();
    expect(screen.getByText("None")).toBeInTheDocument();
    expect(screen.getByText("No backup made with the backup command yet")).toBeInTheDocument();
  });

  it("shows an investor no System section and asks for nothing more", async () => {
    stubFetch({ ...common(), "GET /api/v1/me": { body: ME } });
    renderApp("/about");
    expect(await screen.findByRole("heading", { name: "About" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "System" })).not.toBeInTheDocument();
    // An unexpected request (such as /admin/system) would fail the test.
  });
});

describe("About, the third-party licences (D-167)", () => {
  function lists(routes: Record<string, { status?: number; body?: unknown }>) {
    stubFetch({ ...common(), "GET /api/v1/me": { body: ME }, ...routes });
    renderApp("/about");
  }

  it("lists both sides closed at first, with their counts and the links to the full texts", async () => {
    lists({
      "GET /licenses/frontend.json": { body: FRONTEND },
      "GET /licenses/backend.json": { body: BACKEND },
    });
    const section = await screen.findByRole("region", { name: "Third-party licences" });
    const show = await within(section).findByText("Show the list (3 packages)");
    expect(within(section).getByText("Show the list (2 packages)")).toBeInTheDocument();
    const details = show.closest("details");
    expect(details).not.toHaveAttribute("open");
    const links = within(section).getAllByRole("link", { name: "Read the full licence texts" });
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/licenses/THIRD-PARTY.txt",
      "/licenses/BACKEND-THIRD-PARTY.txt",
    ]);

    fireEvent.click(show);
    expect(details).toHaveAttribute("open");
    const server = within(section).getByRole("table", { name: "Server" });
    const rows = within(server).getAllByRole("row").map((row) => row.textContent);
    expect(rows).toEqual([
      "ComponentVersionLicence",
      "fastapi0.143.0MIT",
      "only-a-file1.0See the licence text",
      "SQLAlchemy2.1.4MIT",
    ]);
    expect(within(section).getByRole("table", { name: "Web app" })).toHaveTextContent(
      "echarts6.1.0Apache-2.0",
    );
  });

  it("says a missing list comes with the built app, and shows the other one", async () => {
    lists({ "GET /licenses/frontend.json": { body: FRONTEND } });
    const section = await screen.findByRole("region", { name: "Third-party licences" });
    expect(
      await within(section).findByText("The list comes with the built app; this is a development run."),
    ).toBeInTheDocument();
    expect(await within(section).findByText("Show the list (2 packages)")).toBeInTheDocument();
    expect(within(section).getAllByRole("link")).toHaveLength(1);
  });

  it("says so for both lists when neither is there, and something that is not a list counts as missing", async () => {
    lists({ "GET /licenses/backend.json": { body: { not: "a list" } } });
    const section = await screen.findByRole("region", { name: "Third-party licences" });
    await waitFor(() =>
      expect(
        within(section).getAllByText("The list comes with the built app; this is a development run."),
      ).toHaveLength(2),
    );
    expect(within(section).queryByRole("link")).not.toBeInTheDocument();
    expect(within(section).queryByRole("table")).not.toBeInTheDocument();
  });
});
