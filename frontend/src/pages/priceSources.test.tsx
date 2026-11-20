import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PriceEngines, PriceRoutes } from "../api/types";
import { formatMoment } from "../format/date";
import { exchangeProblem, leftEmpty, moved } from "../priceSources";
import type { Call } from "../test/fetchStub";
import {
  ADMIN,
  engine,
  ENGINES,
  ROUTES,
  STATUS,
  stubPriceSources,
} from "../test/priceSources";
import { renderApp } from "../test/renderApp";

// The price-sources page (D-196): invented health and counts, never an ISIN or a symbol.

const PAGE = "/admin/price-sources";
const ROUTES_PATH = (role: string) => `PUT /api/v1/admin/market-data/routes/${role}`;

async function open(extra: Parameters<typeof stubPriceSources>[0] = {}) {
  const stub = stubPriceSources(extra);
  const app = renderApp(PAGE);
  await screen.findByRole("heading", { name: "Price sources", level: 1 });
  await screen.findByRole("table", { name: "Sources" });
  await screen.findByRole("heading", { name: "Security type", level: 3 });
  return { ...stub, ...app };
}

const section = (name: string, level = 2) =>
  screen.getByRole("heading", { name, level }).closest("section") as HTMLElement;
const rowOf = (name: string) => screen.getByRole("rowheader", { name: new RegExp(`^${name}`) }).closest("tr") as HTMLElement;
const status = () => screen.getAllByRole("status").find((region) => !region.closest(".ps-run")) as HTMLElement;
const items = (scope: HTMLElement) =>
  within(scope)
    .getAllByRole("listitem")
    .map((item) => item.querySelector(".ps-text-name")?.textContent);
const writes = (calls: Call[]) => calls.filter((call) => call.method !== "GET");

describe("helpers (D-196)", () => {
  it("finds the guarded roles a change leaves without an enabled engine", () => {
    const on = new Set(["yahoo", "frankfurter", "openfigi"]);
    const orders = ROUTES.routes;
    expect(leftEmpty(orders, on, { enabled: new Set(["frankfurter", "openfigi"]) })).toEqual(["ETF_STOCK"]);
    expect(leftEmpty(orders, on, { enabled: new Set(["frankfurter"]) })).toEqual(["ETF_STOCK", "CLASSIFY"]);
    expect(leftEmpty(orders, on, { orders: { FX: [] } })).toEqual(["FX"]);
    expect(leftEmpty(orders, on, { orders: { CLASSIFY: ["yahoo"] } })).toEqual([]);
    // Bonds are not guarded (manual prices by default), and an empty role stays as it was.
    expect(leftEmpty({ ...orders, BOND: [] }, on, { orders: { BOND: [] } })).toEqual([]);
    expect(leftEmpty({ FX: ["frankfurter"] }, new Set(), { orders: { FX: [] } })).toEqual([]);
  });

  it("moves an item and leaves the list when it cannot", () => {
    expect(moved(["a", "b", "c"], 1, -1)).toEqual(["b", "a", "c"]);
    expect(moved(["a", "b", "c"], 1, 1)).toEqual(["a", "c", "b"]);
    expect(moved(["a", "b"], 0, -1)).toEqual(["a", "b"]);
    expect(moved(["a", "b"], 1, 1)).toEqual(["a", "b"]);
  });

  it("checks an exchange as the API does", () => {
    expect(exchangeProblem("  ", [])).toBe("required");
    expect(exchangeProblem("GER,MIL", [])).toBe("comma");
    expect(exchangeProblem("x".repeat(65), [])).toBe("tooLong");
    expect(exchangeProblem(` ${"x".repeat(64)} `, [])).toBeNull();
    expect(exchangeProblem("ger", ["GER"])).toBe("repeat");
    expect(exchangeProblem("NEW", Array.from({ length: 20 }, (_, i) => `X${i}`))).toBe("tooMany");
    expect(exchangeProblem(" PAR ", ["GER"])).toBeNull();
  });
});

describe("the price-sources page (D-196)", () => {
  it("is not found for an investor, as from the API (D-92)", async () => {
    stubPriceSources({ "GET /api/v1/me": { body: { ...ADMIN, role: "INVESTOR" } } });
    renderApp(PAGE);
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("shows the state, the engines with display names, and counts only", async () => {
    await open();
    expect(screen.getByRole("link", { name: "← Administration" })).toHaveAttribute("href", "/admin");
    const run = section("Updates");
    expect(within(run).getByText(`Completed, finished ${formatMoment("2026-11-19T18:31:00Z", "en")}`)).toBeInTheDocument();
    expect(within(run).getByRole("button", { name: "Update now" })).toBeInTheDocument();

    const yahoo = rowOf("Yahoo Finance");
    expect(within(yahoo).getByText("Unofficial")).toBeInTheDocument();
    expect(within(yahoo).getByText("ETFs and stocks, Security type")).toBeInTheDocument();
    expect(within(yahoo).getByRole("switch", { name: "Use Yahoo Finance" })).toBeChecked();
    expect(within(yahoo).getByText("Working")).toBeInTheDocument();
    expect(within(yahoo).getByText("3 found, 1 not found")).toBeInTheDocument();
    expect(
      within(rowOf("OpenFIGI")).getByRole("link", { name: "Terms of use of OpenFIGI" }),
    ).toHaveAttribute("href", "https://www.openfigi.com/docs/terms-of-service");
    expect(within(rowOf("Frankfurter")).getByText("None since the server started")).toBeInTheDocument();
    // Engine keys never show as such (D-196: one display name).
    expect(document.body.textContent).not.toMatch(/\byahoo\b|\bopenfigi\b|\bfrankfurter\b/);
  });

  it("shows a status only next to an enabled engine, with the error code in words", async () => {
    await open({
      "GET /api/v1/admin/market-data/engines": {
        body: {
          market_data: "ON",
          engines: [
            engine("frankfurter", {
              roles: ["FX"],
              status: "PAUSED",
              paused_until: "2026-11-20T10:15:00Z",
              last_error_code: "RATE_LIMITED",
              last_error_at: "2026-11-20T10:00:00Z",
            }),
            engine("keyed", { needs_credential: true, status: "MISSING_CREDENTIAL" }),
            engine("openfigi", { roles: ["CLASSIFY"], status: "EXHAUSTED" }),
            engine("yahoo", { enabled: false, status: "EXHAUSTED" }),
          ],
        } satisfies PriceEngines,
      },
    });
    const frankfurter = rowOf("Frankfurter");
    expect(within(frankfurter).getByText(`Paused until ${formatMoment("2026-11-20T10:15:00Z", "en")}`)).toBeInTheDocument();
    expect(within(frankfurter).getByText("Too many requests")).toBeInTheDocument();
    expect(within(rowOf("keyed")).getByText("Key missing")).toBeInTheDocument();
    expect(within(rowOf("OpenFIGI")).getByText("Stopped until the next update")).toBeInTheDocument();
    const yahoo = rowOf("Yahoo Finance");
    expect(within(yahoo).getByText("Off")).toBeInTheDocument();
    expect(within(yahoo).queryByText("Stopped until the next update")).not.toBeInTheDocument();
  });

  it("turns an engine on and off, and says so", async () => {
    const { calls } = await open({
      "PATCH /api/v1/admin/market-data/engines/openfigi": (call) => ({
        body: engine("openfigi", { roles: ["CLASSIFY"], enabled: (call.body as { enabled: boolean }).enabled }),
      }),
    });
    // Yahoo is still on for the classification, so no confirmation.
    fireEvent.click(within(rowOf("OpenFIGI")).getByRole("switch"));
    await waitFor(() => expect(status()).toHaveTextContent("OpenFIGI is off."));
    expect(writes(calls).map((c) => [c.method, c.path, c.body])).toEqual([
      ["PATCH", "/api/v1/admin/market-data/engines/openfigi", { enabled: false }],
    ]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks before turning off the only engine of a role, and writes nothing on cancel", async () => {
    const { calls } = await open({
      "PATCH /api/v1/admin/market-data/engines/yahoo": {
        body: engine("yahoo", { roles: ["ETF_STOCK", "CLASSIFY"], enabled: false }),
      },
    });
    const toggle = within(rowOf("Yahoo Finance")).getByRole("switch");
    fireEvent.click(toggle);
    const dialog = await screen.findByRole("dialog", { name: "Leave without a source?" });
    expect(within(dialog).getByText(/ETFs and stocks: their prices/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Security type/)).not.toBeInTheDocument(); // OpenFIGI is on
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(writes(calls)).toEqual([]);
    expect(toggle).toBeChecked();

    fireEvent.click(toggle);
    const again = await screen.findByRole("dialog", { name: "Leave without a source?" });
    fireEvent.click(within(again).getByRole("button", { name: "Leave without a source" }));
    await waitFor(() => expect(status()).toHaveTextContent("Yahoo Finance is off."));
    expect(writes(calls)).toHaveLength(1);
  });

  it("asks before leaving the classification without an engine (D-196's reading of D-188 (6))", async () => {
    await open({
      "GET /api/v1/admin/market-data/routes": {
        body: { ...ROUTES, routes: { ...ROUTES.routes, CLASSIFY: ["openfigi"] } },
      },
    });
    fireEvent.click(
      within(section("Security type", 3)).getByRole("button", { name: "Remove OpenFIGI" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Leave without a source?" });
    expect(within(dialog).getByText(/new securities are not recognised/)).toBeInTheDocument();
  });

  it("moves an engine by keyboard: one write, announced, the focus kept on the engine", async () => {
    let routes: PriceRoutes = ROUTES;
    const { calls } = await open({
      "GET /api/v1/admin/market-data/routes": () => ({ body: routes }),
      [ROUTES_PATH("CLASSIFY")]: (call) => {
        routes = { ...routes, routes: { ...routes.routes, CLASSIFY: (call.body as { engines: string[] }).engines } };
        return { body: routes };
      },
    });
    const classify = section("Security type", 3);
    expect(items(classify)).toEqual(["OpenFIGI", "Yahoo Finance"]);
    // The first row has no "Up", the last no "Down".
    expect(within(classify).queryByRole("button", { name: "Up: OpenFIGI" })).not.toBeInTheDocument();
    const down = within(classify).getByRole("button", { name: "Down: OpenFIGI" });
    down.focus();
    fireEvent.click(down);
    await waitFor(() => expect(items(classify)).toEqual(["Yahoo Finance", "OpenFIGI"]));
    expect(status()).toHaveTextContent("OpenFIGI: position 2 of 2 for Security type.");
    // At the bottom "Down" is gone: the focus is on the same engine's "Up".
    expect(document.activeElement).toBe(within(classify).getByRole("button", { name: "Up: OpenFIGI" }));
    expect(writes(calls).map((c) => c.body)).toEqual([{ engines: ["yahoo", "openfigi"] }]);
  });

  it("keeps the same accessible names whatever the visible text (phones show arrows)", async () => {
    await open();
    const button = within(section("Security type", 3)).getByRole("button", { name: "Remove OpenFIGI" });
    expect(button.querySelector(".ps-word")).toHaveTextContent("Remove");
    expect(button.querySelector(".ps-icon")).toHaveAttribute("aria-hidden", "true");
  });

  it("removes and adds an engine; a disabled engine is shown in its place, skipped", async () => {
    let routes: PriceRoutes = {
      ...ROUTES,
      routes: { ...ROUTES.routes, CLASSIFY: ["openfigi", "yahoo"] },
    };
    await open({
      "GET /api/v1/admin/market-data/engines": {
        body: { ...ENGINES, engines: ENGINES.engines.map((e) => (e.name === "openfigi" ? { ...e, enabled: false } : e)) },
      },
      "GET /api/v1/admin/market-data/routes": () => ({ body: routes }),
      [ROUTES_PATH("CLASSIFY")]: (call) => {
        routes = { ...routes, routes: { ...routes.routes, CLASSIFY: (call.body as { engines: string[] }).engines } };
        return { body: routes };
      },
    });
    const classify = section("Security type", 3);
    expect(within(classify).getByText("Off: skipped")).toBeInTheDocument();
    fireEvent.click(within(classify).getByRole("button", { name: "Remove OpenFIGI" }));
    await waitFor(() => expect(items(classify)).toEqual(["Yahoo Finance"]));
    expect(status()).toHaveTextContent("OpenFIGI removed from Security type.");
    expect(document.activeElement).toBe(within(classify).getByRole("button", { name: "Remove Yahoo Finance" }));

    fireEvent.click(within(classify).getByRole("button", { name: "Add OpenFIGI" }));
    await waitFor(() => expect(items(classify)).toEqual(["Yahoo Finance", "OpenFIGI"]));
    expect(status()).toHaveTextContent("OpenFIGI added to Security type at position 2.");
    expect(document.activeElement).toBe(within(classify).getByRole("button", { name: "Up: OpenFIGI" }));
  });

  it("sends the focus to the role's heading when its last engine is removed", async () => {
    let routes: PriceRoutes = ROUTES;
    await open({
      "GET /api/v1/admin/market-data/routes": () => ({ body: routes }),
      [ROUTES_PATH("ETF_STOCK")]: () => {
        routes = { ...routes, routes: { ...routes.routes, ETF_STOCK: [] } };
        return { body: routes };
      },
    });
    const etf = section("ETFs and stocks", 3);
    fireEvent.click(within(etf).getByRole("button", { name: "Remove Yahoo Finance" }));
    const dialog = await screen.findByRole("dialog", { name: "Leave without a source?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Leave without a source" }));
    await within(etf).findByText("No source: nothing is asked for this use.");
    expect(document.activeElement).toBe(within(etf).getByRole("heading", { level: 3 }));
  });

  it("says when a stored engine is unknown to this version, and why the order is refused", async () => {
    await open({
      "GET /api/v1/admin/market-data/routes": {
        body: { ...ROUTES, routes: { ...ROUTES.routes, ETF_STOCK: ["yahoo", "an-engine-of-another-version"] } },
      },
      [ROUTES_PATH("ETF_STOCK")]: { status: 422, body: { code: "VALIDATION_FAILED", detail: "unknown provider" } },
    });
    const etf = section("ETFs and stocks", 3);
    expect(within(etf).getByText("Not available in this version")).toBeInTheDocument();
    fireEvent.click(within(etf).getByRole("button", { name: "Down: Yahoo Finance" }));
    expect(
      await within(etf).findByText("This order holds a source that this version does not have: remove it first."),
    ).toBeInTheDocument();
  });

  it("shows bonds without an engine and without buttons", async () => {
    await open();
    const bonds = section("Bonds", 3);
    expect(within(bonds).getByText("Bonds have no automatic source yet; use manual prices.")).toBeInTheDocument();
    expect(within(bonds).queryByRole("button")).not.toBeInTheDocument();
  });

  it("adds, moves and removes a preferred exchange, with its name when known", async () => {
    let routes: PriceRoutes = ROUTES;
    const { calls } = await open({
      "GET /api/v1/admin/market-data/routes": () => ({ body: routes }),
      "PUT /api/v1/admin/market-data/exchanges": (call) => {
        routes = { ...routes, exchanges: (call.body as { exchanges: string[] }).exchanges };
        return { body: routes };
      },
    });
    const exchanges = section("Preferred exchanges");
    expect(items(exchanges)).toEqual(["GER · Xetra", "MIL · Milan"]);
    const field = within(exchanges).getByLabelText("Add an exchange");
    fireEvent.change(field, { target: { value: " PAR " } });
    fireEvent.click(within(exchanges).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(items(exchanges)).toEqual(["GER · Xetra", "MIL · Milan", "PAR · Paris"]));
    expect(status()).toHaveTextContent("PAR · Paris added to the preferred exchanges at position 3.");
    expect(field).toHaveValue("");

    fireEvent.click(within(exchanges).getByRole("button", { name: "Up: MIL · Milan" }));
    await waitFor(() => expect(items(exchanges)).toEqual(["MIL · Milan", "GER · Xetra", "PAR · Paris"]));
    fireEvent.click(within(exchanges).getByRole("button", { name: "Remove GER · Xetra" }));
    await waitFor(() => expect(items(exchanges)).toEqual(["MIL · Milan", "PAR · Paris"]));
    expect(writes(calls).map((c) => c.body)).toEqual([
      { exchanges: ["GER", "MIL", "PAR"] },
      { exchanges: ["MIL", "GER", "PAR"] },
      { exchanges: ["MIL", "PAR"] },
    ]);
  });

  it("checks an exchange before sending, and shows the API's refusal next to the field", async () => {
    const { calls } = await open({
      "PUT /api/v1/admin/market-data/exchanges": {
        status: 422,
        body: { code: "VALIDATION_FAILED", detail: "invalid exchange" },
      },
    });
    const exchanges = section("Preferred exchanges");
    const field = within(exchanges).getByLabelText("Add an exchange");
    fireEvent.change(field, { target: { value: "ger" } });
    fireEvent.click(within(exchanges).getByRole("button", { name: "Add" }));
    expect(await within(exchanges).findByText("This exchange is already in the list.")).toBeInTheDocument();
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(writes(calls)).toEqual([]);

    fireEvent.change(field, { target: { value: "ÿ" } });
    fireEvent.click(within(exchanges).getByRole("button", { name: "Add" }));
    expect(
      await within(exchanges).findByText("The server refused this exchange: check its code."),
    ).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
  });

  it("says when there is no preferred exchange", async () => {
    await open({ "GET /api/v1/admin/market-data/routes": { body: { ...ROUTES, exchanges: [] } } });
    expect(
      within(section("Preferred exchanges")).getByText(
        "No preference: the main exchanges trading in the security's currency come first.",
      ),
    ).toBeInTheDocument();
  });

  it.each([
    [{ status: 202, body: { queued: "QUEUED" } }, "Update started. New prices arrive over the next minutes."],
    [{ status: 202, body: { queued: "ALREADY_QUEUED" } }, "An update is already waiting to start."],
    [
      { status: 409, body: { code: "MARKET_DATA_RUNNING", detail: "x" } },
      "An update is in progress: try again when it ends.",
    ],
    [
      { status: 409, body: { code: "MARKET_DATA_OFF", detail: "x" } },
      "Automatic prices are off on this installation, so no update can start.",
    ],
  ])("says what came of \"Update now\" in a status region (%#)", async (reply, text) => {
    await open({ "POST /api/v1/admin/market-data/run": reply });
    const run = section("Updates");
    fireEvent.click(within(run).getByRole("button", { name: "Update now" }));
    expect(await within(within(run).getByRole("status")).findByText(text)).toBeInTheDocument();
  });

  it("shows a running update", async () => {
    await open({ "GET /api/v1/admin/market-data/status": { body: { ...STATUS, running: "MANUAL" } } });
    expect(within(section("Updates")).getByText("An update is running now")).toBeInTheDocument();
  });

  it("with market data off: a notice, no \"Update now\", the engines off on this installation", async () => {
    await open({
      "GET /api/v1/admin/market-data/status": { body: { ...STATUS, market_data: "OFF", next_run_at: null } },
      "GET /api/v1/admin/market-data/engines": {
        body: {
          market_data: "OFF",
          engines: ENGINES.engines.map((e) => ({ ...e, off_by_environment: true })),
        },
      },
      "GET /api/v1/admin/market-data/routes": { body: { ...ROUTES, market_data: "OFF" } },
    });
    expect(screen.getByText(/Automatic prices are off on this installation \(PT_MARKET_DATA\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Update now" })).not.toBeInTheDocument();
    expect(within(rowOf("Yahoo Finance")).getByText("Off on this installation")).toBeInTheDocument();
    // Still editable: the change is stored for the first run (D-188 (3)).
    expect(within(rowOf("Yahoo Finance")).getByRole("switch")).toBeInTheDocument();
  });

  it("shows a load error with a retry in the card that failed", async () => {
    stubPriceSources({
      "GET /api/v1/admin/market-data/engines": { status: 503, body: { code: "X", detail: "x" } },
    });
    renderApp(PAGE);
    await screen.findByRole("heading", { name: "Price sources", level: 1 });
    const engines = await waitFor(() => section("Sources"));
    expect(await within(engines).findByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Security type", level: 3 })).toBeInTheDocument();
  });
});
