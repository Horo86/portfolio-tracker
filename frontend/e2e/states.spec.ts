import { expect, test } from "@playwright/test";

import { apiPost, expectAccessible, logIn, newUser } from "./helpers";

// The empty and error states of mockup 19, each checked by axe (WCAG 2.1 A and AA; the heading
// levels are checked in the unit tests). Invented accounts and files only.

type Created = { id: string };

test("the empty states, the no results card and the page not found", async ({ page }) => {
  await logIn(page, await newUser("states", { language: "it" }));
  await expect(page.getByText("In tre passi sei operativo:")).toBeVisible();
  await expectAccessible(page, "dashboard before the first account");

  const account = await apiPost<Created>(page, "/accounts", { name: "Conto vuoto", currency: "EUR" });
  await page.goto(`/?account=${account.id}`);
  await expect(
    page.getByRole("heading", { name: "Questo conto non ha ancora titoli", level: 2 }),
  ).toBeVisible();
  await expectAccessible(page, "dashboard of an account with no transactions");

  await apiPost(page, "/transactions", {
    account_id: account.id, type: "DEPOSIT", trade_date: "2026-01-05", total_amount: "100.00",
  }); // prettier-ignore
  await page.goto("/transactions?type=DIVIDEND");
  await expect(page.getByRole("heading", { name: "Nessun risultato", level: 2 })).toBeVisible();
  await expect(page.getByText("Nessuna transazione corrisponde ai filtri scelti (tipo: Dividendo).")).toBeVisible();
  await expectAccessible(page, "transactions with no results");
  await page.getByRole("button", { name: "Azzera i filtri" }).click();
  await expect(page).toHaveURL("/transactions");
  await expect(page.getByRole("row", { name: /Versamento/ })).toBeVisible();

  await page.goto("/no/such/page");
  await expect(page.getByRole("heading", { name: "Pagina non trovata", level: 1 })).toBeVisible();
  await expectAccessible(page, "page not found");
  await page.getByRole("link", { name: "Torna alla dashboard" }).click();
  await expect(page).toHaveURL("/");
});

test("a file not recognized, then a load that failed", async ({ page }) => {
  await logIn(page, await newUser("states", { language: "it" }));
  await apiPost(page, "/accounts", { name: "Conto", currency: "EUR" });
  await page.goto("/import");
  // A text file in neither format: the fixtures' own README.
  await page.getByLabel("File da importare").setInputFiles("../tests/fixtures/directa/README.md");
  await page.getByRole("button", { name: "Continua all'anteprima" }).click();
  const card = page.getByRole("alert").filter({ hasText: "File non riconosciuto" });
  await expect(card).toContainText("Il file «README.md» non ha il formato Directa");
  await expect(card.getByRole("region", { name: "Colonne attese" })).toContainText("Data operazione");
  await card.getByText("Quali formati sono supportati?").click();
  await expect(card.getByText(/Formati supportati: Directa/)).toBeVisible();
  await expectAccessible(page, "import, file not recognized");
  await card.getByRole("button", { name: "Scegli un altro file" }).click();
  await expect(page.getByText("Trascina qui il file")).toBeVisible();

  // The server's answer is replaced here only: nothing fails on the server.
  await page.route("**/api/v1/accounts", (route) =>
    route.fulfill({ status: 500, json: { code: "INTERNAL_ERROR", detail: "x" } }),
  );
  await page.goto("/accounts");
  const error = page.getByRole("alert").filter({ hasText: "Qualcosa non ha funzionato" });
  // An investor cannot see the container: the administrator is told (an administrator gets the
  // logs hint, unit tests).
  await expect(error).toContainText("Se succede ancora, avvisa l'amministratore.");
  await expect(error).toContainText("Codice: INTERNAL_ERROR.");
  await expectAccessible(page, "accounts, load failed");
  await page.unroute("**/api/v1/accounts");
  await error.getByRole("button", { name: "Riprova" }).click();
  await expect(page.getByRole("region", { name: "Conto" })).toBeVisible();
});
