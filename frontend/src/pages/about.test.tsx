import { screen } from "@testing-library/react";
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

function common() {
  return {
    "GET /api/health": HEALTH,
    "GET /api/v1/accounts": NO_ACCOUNTS,
    "GET /api/v1/setup/status": { body: { setup_required: false } },
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
