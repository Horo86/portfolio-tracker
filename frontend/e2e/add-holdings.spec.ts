import { expect, test } from "@playwright/test";

import { apiPost, logIn, newUser } from "./helpers";

// Invented accounts, securities, ISINs and amounts only. The seeded server runs with market
// data off (PT_MARKET_DATA=false): the search page says so and never asks the server.

type Created = { id: string };
type Asset = { id: string; name: string; isin: string | null; quote_currency: string };

const ISIN = "ZZ0000087039"; // invented, valid check digit

test("the search page with market data off: no search sent, a security created by hand", async ({
  page,
}) => {
  await logIn(page, await newUser("search-off"));
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const searches: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/assets/search")) searches.push(request.url());
  });

  await page.goto(`/holdings/add?account=${account.id}`);
  await expect(page.getByRole("heading", { name: "Add a security", level: 1 })).toBeVisible();
  await expect(
    page.getByText(/Automatic prices are off on this installation: the search is not available/),
  ).toBeVisible();
  await expect(page.getByLabel("Search by ISIN, name or symbol")).toBeDisabled();
  // Even a submit forced past the disabled button sends nothing.
  await page.getByRole("search").evaluate((form) => (form as HTMLFormElement).requestSubmit());

  await page.getByLabel("Name", { exact: true }).fill("Esempio Mondo Locale ETF");
  await page.getByLabel("ISIN (optional)").fill(ISIN);
  await page.getByRole("button", { name: "Create security" }).click();
  await expect(
    page.getByText(
      "“Esempio Mondo Locale ETF” added. Automatic prices are off on this installation.",
    ),
  ).toBeVisible();
  expect(searches).toEqual([]);

  const assets = (await (await page.request.get("/api/v1/assets")).json()) as Asset[];
  const created = assets.find((a) => a.isin === ISIN);
  expect(created).toMatchObject({ name: "Esempio Mondo Locale ETF", quote_currency: "EUR" });
  const next = `account=${account.id}&asset=${created!.id}`;
  await expect(page.getByRole("link", { name: "Add holdings I already own" })).toHaveAttribute(
    "href",
    `/holdings/existing?${next}`,
  );
  await page.getByRole("link", { name: "Add a transaction" }).click();
  await expect(page.getByRole("heading", { name: "New transaction", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Security", { exact: true })).toHaveValue(created!.id);
  await expect(page.getByLabel("Account", { exact: true })).toHaveValue(account.id);

  // The same ISIN again: the page links to the security the user already has.
  await page.goto(`/holdings/add?account=${account.id}`);
  await page.getByLabel("Name", { exact: true }).fill("Esempio Doppio");
  await page.getByLabel("ISIN (optional)").fill(ISIN);
  await page.getByRole("button", { name: "Create security" }).click();
  await expect(
    page.getByText("You already have this security: “Esempio Mondo Locale ETF”."),
  ).toBeVisible();
  await page.getByRole("link", { name: "Open" }).click();
  await expect(page).toHaveURL(`/holdings/${created!.id}?account=${account.id}`);
});
