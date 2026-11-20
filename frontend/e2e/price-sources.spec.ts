import { MARKET } from "../playwright.config";
import { expect, type Page, test } from "./fixtures";
import { expectAccessible, logIn } from "./helpers";
import { ensureMarketAdmin, MARKET_ADMIN as ADMIN } from "./marketUsers";
import { restore, type Snapshot, snapshot } from "./priceSourcesPage";

// The price-sources page (screen 17, D-196) on the market server (:8712, invented fakes: Yahoo
// Finance for ETFs, stocks and the classification, OpenFIGI for the classification,
// Frankfurter for rates). Each test puts the configuration back as it found it and waits for
// the re-check runs its changes queued, so the specs that run after it see the defaults.

test.use({ baseURL: MARKET });
test.describe.configure({ mode: "serial" });

let saved: Snapshot;
test.beforeAll(async () => {
  await ensureMarketAdmin();
  saved = await snapshot();
});
test.afterEach(async () => {
  await restore(saved);
});

async function openPage(page: Page) {
  await logIn(page, ADMIN);
  await page.goto("/admin");
  await page.getByRole("link", { name: "Manage the price sources" }).click();
  await expect(page.getByRole("heading", { name: "Price sources", level: 1 })).toBeFocused();
  await expect(page.getByRole("table", { name: "Sources" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Security type", level: 3 })).toBeVisible();
}

const region = (page: Page, name: string, level = 2) =>
  page.locator("section", { has: page.getByRole("heading", { name, level, exact: true }) }).last();
const said = (page: Page) => page.locator("main [role=status]:not(.ps-run [role=status])").first();
const names = (page: Page, name: string, level = 3) => region(page, name, level).locator(".ps-text-name");

test("the page from Administration: the engines with their names, axe", async ({ page }) => {
  await openPage(page);
  const yahoo = page.getByRole("row", { name: /^Yahoo Finance/ });
  await expect(yahoo).toContainText("Unofficial");
  await expect(yahoo.getByRole("switch", { name: "Use Yahoo Finance" })).toBeChecked();
  await expect(yahoo).toContainText("Working");
  await expect(
    page.getByRole("row", { name: /^OpenFIGI/ }).getByRole("link", { name: "Terms of use of OpenFIGI" }),
  ).toHaveAttribute("href", "https://www.openfigi.com/docs/terms-of-service");
  await expect(names(page, "Security type")).toHaveText(["OpenFIGI", "Yahoo Finance"]);
  await expect(page.locator("main")).not.toContainText(/\byahoo\b|\bopenfigi\b|\bfrankfurter\b/);
  await expectAccessible(page, "the price-sources page");
  // Back to Administration, whose card names the engines the same way.
  await page.getByRole("link", { name: "← Administration" }).click();
  await expect(page.getByRole("heading", { name: "Administration", level: 1 })).toBeVisible();
  await expect(region(page, "Price sources")).toContainText("Yahoo Finance");
});

test("an engine off and on by keyboard; off, it is skipped in its orders", async ({ page }) => {
  await openPage(page);
  const toggle = page.getByRole("switch", { name: "Use OpenFIGI" });
  await toggle.focus();
  await page.keyboard.press("Space"); // Yahoo Finance still classifies: no confirmation
  await expect(said(page)).toHaveText("OpenFIGI is off.");
  await expect(toggle).not.toBeChecked();
  await expect(page.getByRole("row", { name: /^OpenFIGI/ })).toContainText("Off");
  await expect(region(page, "Security type", 3).getByText("Off: skipped")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("switch", { name: "Use OpenFIGI" })).not.toBeChecked(); // stored
  await page.getByRole("switch", { name: "Use OpenFIGI" }).focus();
  await page.keyboard.press("Space");
  await expect(said(page)).toHaveText("OpenFIGI is on.");
  await expect(region(page, "Security type", 3).getByText("Off: skipped")).toHaveCount(0);
});

test("reorder by keyboard only, announced, the focus kept on the engine", async ({ page }) => {
  await openPage(page);
  const classify = region(page, "Security type", 3);
  await classify.getByRole("button", { name: "Down: OpenFIGI" }).focus();
  await page.keyboard.press("Enter");
  await expect(names(page, "Security type")).toHaveText(["Yahoo Finance", "OpenFIGI"]);
  await expect(said(page)).toHaveText("OpenFIGI: position 2 of 2 for Security type.");
  // At the bottom "Down" is gone: the focus is on the same engine's "Up".
  await expect(classify.getByRole("button", { name: "Up: OpenFIGI" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(names(page, "Security type")).toHaveText(["OpenFIGI", "Yahoo Finance"]);
  await expect(said(page)).toHaveText("OpenFIGI: position 1 of 2 for Security type.");
  await expect(classify.getByRole("button", { name: "Down: OpenFIGI" })).toBeFocused();
});

test("removing the only engine of a role asks first; cancel writes nothing", async ({ page }) => {
  await openPage(page);
  const etf = region(page, "ETFs and stocks", 3);
  const remove = etf.getByRole("button", { name: "Remove Yahoo Finance" });
  await remove.click();
  const dialog = page.getByRole("dialog", { name: "Leave without a source?" });
  await expect(dialog).toContainText("ETFs and stocks: their prices are no longer updated.");
  await expectAccessible(page, "the confirmation dialog");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(remove).toBeFocused();
  await page.reload();
  await expect(names(page, "ETFs and stocks")).toHaveText(["Yahoo Finance"]); // nothing written
});

test("turning off the only rate engine asks first; confirmed, it is off, then on again", async ({
  page,
}) => {
  await openPage(page);
  await page.getByRole("switch", { name: "Use Frankfurter" }).click();
  const dialog = page.getByRole("dialog", { name: "Leave without a source?" });
  await expect(dialog).toContainText("Exchange rates: they are no longer updated.");
  await dialog.getByRole("button", { name: "Leave without a source" }).click();
  await expect(said(page)).toHaveText("Frankfurter is off.");
  await expect(page.getByRole("switch", { name: "Use Frankfurter" })).toBeFocused();
  await page.getByRole("switch", { name: "Use Frankfurter" }).click(); // on: no question
  await expect(said(page)).toHaveText("Frankfurter is on.");
});

test("preferred exchanges: checked before sending, added, moved by keyboard, removed", async ({
  page,
}) => {
  await openPage(page);
  const exchanges = region(page, "Preferred exchanges");
  await expect(exchanges).toContainText("No preference");
  const field = exchanges.getByLabel("Add an exchange");
  await field.fill("GER");
  await field.press("Enter");
  await expect(said(page)).toHaveText("GER · Xetra added to the preferred exchanges at position 1.");
  await expect(field).toHaveValue("");
  await field.fill(" mil ");
  await exchanges.getByRole("button", { name: "Add", exact: true }).click();
  await expect(names(page, "Preferred exchanges", 2)).toHaveText(["GER · Xetra", "mil"]);
  await field.fill("ger");
  await field.press("Enter");
  await expect(exchanges.getByText("This exchange is already in the list.")).toBeVisible();
  await expect(field).toHaveAttribute("aria-invalid", "true");
  await field.fill("x".repeat(65));
  await field.press("Enter");
  await expect(exchanges.getByText("At most 64 characters.")).toBeVisible();
  await expectAccessible(page, "an exchange's error");

  await exchanges.getByRole("button", { name: "Up: mil" }).focus();
  await page.keyboard.press("Enter");
  await expect(names(page, "Preferred exchanges", 2)).toHaveText(["mil", "GER · Xetra"]);
  await expect(said(page)).toHaveText("mil: position 1 of 2 in the preferred exchanges.");
  await expect(exchanges.getByRole("button", { name: "Down: mil" })).toBeFocused();
  await exchanges.getByRole("button", { name: "Remove mil" }).click();
  await expect(names(page, "Preferred exchanges", 2)).toHaveText(["GER · Xetra"]);
  await expect(exchanges.getByRole("button", { name: "Remove GER · Xetra" })).toBeFocused();
});

test("update now: its answer said in the run's status region", async ({ page }) => {
  await openPage(page);
  const run = region(page, "Updates");
  await run.getByRole("button", { name: "Update now" }).click();
  await expect(run.getByRole("status")).toHaveText(
    /^(Update started\. New prices arrive over the next minutes\.|An update is already waiting to start\.|An update is in progress: try again when it ends\.)$/,
  );
});

test("update now while a run is in progress: a readable message", async ({ page }) => {
  await page.route("**/api/v1/admin/market-data/run", (route) =>
    route.fulfill({ status: 409, json: { code: "MARKET_DATA_RUNNING", detail: "x" } }),
  );
  await openPage(page);
  const run = region(page, "Updates");
  await run.getByRole("button", { name: "Update now" }).click();
  await expect(run.getByRole("status")).toHaveText("An update is in progress: try again when it ends.");
  await expectAccessible(page, "the run's message");
});
