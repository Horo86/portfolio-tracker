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
    await page.getByLabel("Import into account").selectOption({ label: "Conto Directa" });
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(page.locator(".import-blocking")).toBeVisible();
    await expectAccessible(page, `import preview with blocking, ${theme}`);
    await page.getByRole("tab", { name: "Not recognized (1)" }).click();
    await expectAccessible(page, `import preview, not recognized tab, ${theme}`);
  });
}
