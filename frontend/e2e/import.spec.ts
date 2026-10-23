import { expect, type Page, test } from "@playwright/test";

import { apiPatch, apiPost, expectAccessible, logIn, newUser } from "./helpers";

// The synthetic Directa fixture of the repository (invented account B0000, invented ISINs and
// amounts: tests/fixtures/directa/README.md) and invented accounts only.
// Relative to the frontend folder, where `npm run e2e` runs.
const DIRECTA_FIXTURE = "../tests/fixtures/directa/directa_synthetic_2024.xlsx";

type Created = { id: string };

test.beforeEach(async ({ page }) => {
  await logIn(page, await newUser("import"));
});

async function openImport(page: Page) {
  await page.getByRole("link", { name: "Import", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Import transactions", level: 1 })).toBeVisible();
}

/** Chooses a file made in the browser: `size` bytes of "a", or the given text. */
async function chooseMadeFile(page: Page, name: string, content: { text?: string; size?: number }) {
  await page.getByLabel("File to import").evaluate(
    (input: HTMLInputElement, { name, text, size }) => {
      const data = new DataTransfer();
      const body = text ?? "a".repeat(size ?? 0);
      data.items.add(new File([body], name, { type: "text/csv" }));
      input.files = data.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { name, ...content },
  );
}

test("the Directa fixture asks for its account, then shows the preview", async ({ page }) => {
  await apiPost<Created>(page, "/accounts", { name: "Conto Directa", currency: "EUR" });
  await apiPost<Created>(page, "/accounts", { name: "Conto Dollari", currency: "USD" });
  await page.reload();
  await openImport(page);
  await page.getByLabel("File to import").setInputFiles(DIRECTA_FIXTURE);
  await expect(page.getByText(/Chosen file: directa_synthetic_2024\.xlsx/)).toBeVisible();
  await page.getByRole("button", { name: "Continue to the preview" }).click();

  // B0000 is remembered by no account yet: the account step, euro accounts only.
  const account = page.getByLabel("Import into account");
  await expect(account).toBeVisible();
  await expect(account.locator("option")).toHaveText(["Choose an account…", "Conto Directa"]);
  await account.selectOption({ label: "Conto Directa" });
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  await expect(page.getByText("directa_synthetic_2024.xlsx", { exact: true })).toBeVisible();
  await expect(page.getByText("Format: Directa (Excel) · 22 rows read")).toBeVisible();
  await expect(page.getByLabel("Import into account")).toHaveValue(/.+/);
});

test("the Directa fixture with no account yet: create it from the account step", async ({
  page,
}) => {
  await openImport(page);
  await page.getByLabel("File to import").setInputFiles(DIRECTA_FIXTURE);
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await expect(page.getByText("You have no account in euro yet.")).toBeVisible();

  await page.getByRole("button", { name: "New account…" }).click();
  const dialog = page.getByRole("dialog", { name: "New account" });
  await dialog.getByLabel("Name").fill("Conto Directa");
  await expect(dialog.getByLabel("Currency")).toHaveValue("EUR");
  await expect(dialog.getByLabel("Currency")).toBeDisabled();
  await dialog.getByRole("button", { name: "Create account" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel("Import into account")).toHaveValue(/[0-9a-f-]{36}/);
  await expect(page.getByText("Choose the account to import into.")).toHaveCount(0);

  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByText("Format: Directa (Excel) · 22 rows read")).toBeVisible();
  await expect(page.getByLabel("Import into account")).toHaveValue(/.+/);
});

test("a file over 10 MB is refused in the browser and never sent", async ({ page }) => {
  const sent: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/imports/")) sent.push(request.url());
  });
  await openImport(page);
  await chooseMadeFile(page, "grande.csv", { size: 10 * 1024 * 1024 + 1 });
  const refused = page.getByRole("alert").filter({ hasText: "larger than 10 MB" });
  await expect(refused).toBeVisible();
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await expect(refused).toBeVisible();
  expect(sent).toEqual([]);
});

/** The fixture's preview in a new euro account "Conto Directa", through the account step. */
async function previewFixture(page: Page) {
  await apiPost<Created>(page, "/accounts", { name: "Conto Directa", currency: "EUR" });
  await page.reload();
  await openImport(page);
  await page.getByLabel("File to import").setInputFiles(DIRECTA_FIXTURE);
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await page.getByLabel("Import into account").selectOption({ label: "Conto Directa" });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("tablist", { name: "Lines by category" })).toBeVisible();
}

const card = (page: Page, label: string) => page.locator(".import-count", { hasText: label });

test("the fixture's preview: counts, security types, ticks and the option", async ({ page }) => {
  await previewFixture(page);
  const selected = card(page, "Transactions to import");
  const warnings = card(page, "Warnings to read");
  await expect(selected.locator("dd")).toHaveText("11");
  await expect(card(page, "Already present, skipped").locator("dd")).toHaveText("0");
  await expect(card(page, "Not imported").locator("dd")).toHaveText("1");
  await expect(page.getByRole("tab")).toHaveText([
    "To import (11)",
    "To review (1)",
    "Not recognized (1)",
  ]);

  // Two new securities need a type; the bond is proposed from the file (D-102).
  const blocking = page.locator(".import-blocking");
  await expect(blocking).toContainText("Choose the type of TEST ETF WORLD UCITS.");
  await expect(blocking).toContainText("Choose the type of TEST AZIONE SPA.");
  const asset = (name: string) => page.locator(".import-asset", { hasText: name });
  await expect(asset("TEST BTP 2,0% 2030").getByLabel("Type")).toHaveValue("BOND");
  await asset("TEST ETF WORLD UCITS").getByLabel("Type").selectOption({ label: "ETF" });
  await asset("TEST AZIONE SPA").getByLabel("Type").selectOption({ label: "Stock" });
  await expect(blocking).toHaveCount(0);

  // The coupon of the bond whose purchase is still to review.
  await expect(warnings.locator("dd")).toHaveText("1");
  await expect(page.locator(".import-warnings")).toContainText(
    "On Sep 16, 2024 Conto Directa does not hold this security. (rows 11, 12)",
  );
  await expect(page.getByRole("row", { name: /Coupon/ })).toContainText(
    "Its purchase (T0000000000004) is still to review.",
  );

  // Tick the bond's purchase: 12, and the warning goes.
  await page.getByRole("tab", { name: "To review (1)" }).click();
  const bond = page.getByRole("checkbox", { name: "Import: Buy, TEST BTP 2,0% 2030, Mar 15, 2024" });
  await bond.check();
  await expect(selected.locator("dd")).toHaveText("12");
  await expect(warnings.locator("dd")).toHaveText("0");
  await bond.uncheck();
  await expect(selected.locator("dd")).toHaveText("11");

  // Without the first deposit the cash goes below zero.
  await page.getByRole("tab", { name: "To import (11)" }).click();
  await page.getByRole("checkbox", { name: "Import: Deposit, Jan 10, 2024" }).uncheck();
  await expect(selected.locator("dd")).toHaveText("10");
  await expect(warnings.locator("dd")).toHaveText("2");
  await expect(page.locator(".import-warnings")).toContainText(
    "The cash of Conto Directa goes below zero on Jan 15, 2024.",
  );

  // "Do not move cash": the ticks go back to the default, deposits and expenses are skipped.
  await page.getByLabel("Do not move cash").check();
  await expect(selected.locator("dd")).toHaveText("7");
  await expect(page.getByRole("tab", { name: "Skipped by choice (4)" })).toBeVisible();
  await expect(warnings.locator("dd")).toHaveText("1");
  await page.getByRole("tab", { name: "To review (1)" }).click();
  await bond.check();
  await expect(selected.locator("dd")).toHaveText("8");
  await expect(warnings.locator("dd")).toHaveText("0");
});

/** Chooses the types the fixture's new securities need (the bond is proposed, D-102). */
async function chooseFixtureTypes(page: Page) {
  const asset = (name: string) => page.locator(".import-asset", { hasText: name });
  await asset("TEST ETF WORLD UCITS").getByLabel("Type").selectOption({ label: "ETF" });
  await asset("TEST AZIONE SPA").getByLabel("Type").selectOption({ label: "Stock" });
  await expect(page.locator(".import-blocking")).toHaveCount(0);
}

const confirmButton = (page: Page, count: number) =>
  page.getByRole("button", {
    name: `Confirm the import of ${count} transaction${count === 1 ? "" : "s"}`,
  });

test("the fixture is imported once: the second time its lines are already present", async ({
  page,
}) => {
  await previewFixture(page);
  await chooseFixtureTypes(page);
  await expect(
    page.getByText("11 transactions will be added to the account «Conto Directa»."),
  ).toBeVisible();
  await confirmButton(page, 11).click();

  await expect(
    page.getByRole("heading", { name: "Done: 11 transactions added to «Conto Directa»" }),
  ).toBeVisible();
  await expect(card(page, "Added").locator("dd")).toHaveText("11");
  await expect(card(page, "Already present, skipped").locator("dd")).toHaveText("0");
  await expect(card(page, "Not imported").locator("dd")).toHaveText("1");
  await expect(card(page, "Warnings to check").locator("dd")).toHaveText("1");
  await expect(
    page.getByText("1 line to review was not ticked and was not imported."),
  ).toBeVisible();
  await page.getByRole("link", { name: "Go to the transactions" }).click();
  await expect(page.getByText("Rows 1–11 of 11", { exact: true })).toBeVisible();

  // Again: the account remembers B0000, so no account step; nothing new to import.
  await openImport(page);
  await page.getByLabel("File to import").setInputFiles(DIRECTA_FIXTURE);
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await expect(page.getByRole("tab", { name: "Already present (11)" })).toBeVisible();
  await expect(card(page, "Transactions to import").locator("dd")).toHaveText("0");
  await expect(confirmButton(page, 0)).toBeDisabled();
  await expect(page.getByText("Nothing to import: tick at least one line.")).toBeVisible();

  // The bond's purchase, still to review, can be added on its own.
  await page.getByRole("tab", { name: "To review (1)" }).click();
  await page.getByRole("checkbox", { name: /Import: Buy, TEST BTP 2,0% 2030/ }).check();
  await confirmButton(page, 1).click();
  await expect(
    page.getByRole("heading", { name: "Done: 1 transaction added to «Conto Directa»" }),
  ).toBeVisible();
  await expect(card(page, "Already present, skipped").locator("dd")).toHaveText("11");
});

// An invented app CSV (D-115): an account without currency, a new security without its price
// currency, a sale of more than is held (row 5) and a type the format does not know (row 6).
const HAND_CSV = [
  "account,account_currency,trade_date,type,isin,ticker,asset_name,quantity,unit_price,gross_amount,fees,taxes,total_amount,external_ref",
  "Conto CSV,,2026-03-02,DEPOSIT,,,,,,,,,1000.00,E2E-1",
  "Conto CSV,,2026-03-03,BUY,ZZ0000002012,,Esempio Nuovo ETF,10,12.50,125.00,1.00,,126.00,E2E-2",
  "Conto CSV,,2026-03-04,BUY,ZZ0000002012,,Esempio Nuovo ETF,5,20.00,100.00,1.00,,101.00,E2E-3",
  "Conto CSV,,2026-03-05,SELL,ZZ0000002012,,Esempio Nuovo ETF,30,13.00,390.00,1.00,,389.00,E2E-4",
  "Conto CSV,,2026-03-06,PIPPO,,,,,,,,,10.00,E2E-5",
].join("\n");

test("a CSV: create its account, choose the security, untick a conflict", async ({ page }) => {
  await openImport(page);
  await chooseMadeFile(page, "esempio.csv", { text: HAND_CSV });
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  const blocking = page.locator(".import-blocking");
  await expect(blocking).toContainText("The account «Conto CSV» of the file has no currency");

  await page.getByRole("button", { name: "Create the account «Conto CSV»…" }).click();
  const dialog = page.getByRole("dialog", { name: "New account" });
  await expect(dialog.getByLabel("Name")).toHaveValue("Conto CSV");
  await dialog.getByLabel("Currency").selectOption("EUR");
  await dialog.getByRole("button", { name: "Create account" }).click();
  await expect(blocking).not.toContainText("has no currency");
  await expect(page.getByLabel("«Conto CSV» goes into")).toHaveValue(/[0-9a-f-]{36}/);
  await expect(page.getByLabel("«Conto CSV» goes into")).toBeFocused();

  const etf = page.locator(".import-asset", { hasText: "Esempio Nuovo ETF" });
  await etf.getByLabel("Type").selectOption({ label: "ETF" });
  await etf.getByLabel("Price currency").selectOption("EUR");
  await expect(blocking).toContainText("Conto CSV:");
  await expect(blocking).toContainText("row 5");
  await expect(page.getByRole("tab", { name: "Not valid (1)" })).toBeVisible();

  await page
    .getByRole("checkbox", { name: /Import: Sell, Esempio Nuovo ETF, Mar 5, 2026/ })
    .uncheck();
  await expect(blocking).toHaveCount(0);
  await expect(card(page, "Transactions to import").locator("dd")).toHaveText("3");
  await confirmButton(page, 3).click();
  await expect(
    page.getByRole("heading", { name: "Done: 3 transactions added to «Conto CSV»" }),
  ).toBeVisible();
  await expect(page.getByText("1 line was unticked or skipped by choice.")).toBeVisible();
});

test("a busy server: the confirmation waits, then goes", async ({ page }) => {
  let busy = 1;
  await page.route("**/api/v1/imports/confirm", async (route) => {
    if (busy-- > 0) {
      await route.fulfill({
        status: 503,
        headers: { "Retry-After": "3" },
        contentType: "application/json",
        body: JSON.stringify({ code: "IMPORT_BUSY", detail: "busy" }),
      });
    } else {
      await route.continue();
    }
  });
  await previewFixture(page);
  await chooseFixtureTypes(page);
  await confirmButton(page, 11).click();
  await expect(page.getByText(/trying again in \d s \(attempt 1 of 3\)/)).toBeVisible();
  // The preview being confirmed cannot change meanwhile: it is inert, nothing takes the focus.
  const untick = page.getByRole("button", { name: "Untick all" });
  await untick.focus();
  await expect(untick).not.toBeFocused();
  await expect(
    page.getByRole("heading", { name: "Done: 11 transactions added to «Conto Directa»" }),
  ).toBeVisible();
});

test("the app's own export, imported by another user, then again", async ({ page }) => {
  await previewFixture(page);
  await chooseFixtureTypes(page);
  await page.getByRole("tab", { name: "To review (1)" }).click();
  await page.getByRole("checkbox", { name: /Import: Buy, TEST BTP 2,0% 2030/ }).check();
  await confirmButton(page, 12).click();
  await expect(page.getByRole("heading", { name: /^Done: 12 transactions/ })).toBeVisible();

  await page.getByRole("link", { name: "Settings", exact: true }).click();
  const downloading = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download CSV" }).click();
  const exported = await (await downloading).path();

  await page.getByRole("button", { name: "Log out", exact: true }).click();
  await logIn(page, await newUser("import-other"));
  await openImport(page);
  await page.getByLabel("File to import").setInputFiles(exported);
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await expect(page.getByLabel("«Conto Directa» goes into")).toHaveValue("new");
  await expect(card(page, "Transactions to import").locator("dd")).toHaveText("12");
  await confirmButton(page, 12).click();
  await expect(
    page.getByRole("heading", { name: "Done: 12 transactions added to «Conto Directa»" }),
  ).toBeVisible();
  await expect(page.getByText("1 account created.")).toBeVisible();

  await page.getByRole("button", { name: "Import another file" }).click();
  await page.getByLabel("File to import").setInputFiles(exported);
  await page.getByRole("button", { name: "Continue to the preview" }).click();
  await expect(page.getByRole("tab", { name: "Already present (12)" })).toBeVisible();
  await expect(confirmButton(page, 0)).toBeDisabled();
});

for (const theme of ["sobrio", "moderno", "ambra", "grafite"] as const) {
  test(`no axe violation on the import screens in the ${theme} theme`, async ({ page }) => {
    await apiPatch(page, "/me", { theme });
    await apiPost<Created>(page, "/accounts", { name: "Conto Directa", currency: "EUR" });
    await page.reload();
    await openImport(page);
    await page.getByText("Known limits").click();
    await expectAccessible(page, `import file step, ${theme}`);
    await page.getByLabel("File to import").setInputFiles(DIRECTA_FIXTURE);
    await page.getByRole("button", { name: "Continue to the preview" }).click();
    await expect(page.getByLabel("Import into account")).toBeVisible();
    await expectAccessible(page, `import account step, ${theme}`);
    await page.getByRole("button", { name: "New account…" }).click();
    await expectAccessible(page, `import account step, new account dialog, ${theme}`);
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    await page.getByLabel("Import into account").selectOption({ label: "Conto Directa" });
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(page.locator(".import-blocking")).toBeVisible();
    await expectAccessible(page, `import preview with blocking, ${theme}`);
    await page.getByRole("tab", { name: "Not recognized (1)" }).click();
    await expectAccessible(page, `import preview, not recognized tab, ${theme}`);
    await chooseFixtureTypes(page);
    await confirmButton(page, 11).click();
    await expect(page.getByRole("heading", { name: /^Done: 11 transactions/ })).toBeVisible();
    await expectAccessible(page, `import summary, ${theme}`);
    await page.getByRole("button", { name: "Import another file" }).click();
    await chooseMadeFile(page, "esempio.csv", { text: HAND_CSV });
    await page.getByRole("button", { name: "Continue to the preview" }).click();
    await page.getByRole("button", { name: "Create the account «Conto CSV»…" }).click();
    await expectAccessible(page, `import preview, new account dialog, ${theme}`);
  });
}
