import { expect, type Page, test } from "@playwright/test";

import { apiPost, logIn, newUser } from "./helpers";

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

test("the Directa fixture asks for its account, then shows the file's preview", async ({ page }) => {
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
