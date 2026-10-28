import { expect, type Page, test } from "@playwright/test";

import { apiPost, logIn, newUser } from "./helpers";

// Screen 12 (D-149): a user's manual prices, entered, edited and deleted, and what they do to
// the value of a holding. Invented securities and amounts only; market data is off on this
// server, so no automatic price ever arrives.

type Created = { id: string };

/** The day after `day` (YYYY-MM-DD). */
function nextDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date + 1)).toISOString().slice(0, 10);
}

/** The date field's text (dd/mm/yyyy) as an API day, and back. */
const apiDay = (typed: string) => typed.split("/").reverse().join("-");
const typedDay = (day: string) => day.split("-").reverse().join("/");

/** A day as the page writes it in `language` (medium style, as `format/date.ts`). */
function shown(day: string, language: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Intl.DateTimeFormat(language, { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, date)),
  );
}

async function holding(page: Page, asset: Record<string, string>, buy: Record<string, string>) {
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const created = await apiPost<Created>(page, "/assets", asset);
  await apiPost(page, "/transactions", {
    account_id: account.id,
    type: "BUY",
    trade_date: "2026-03-02",
    asset_id: created.id,
    affects_cash: false,
    ...buy,
  });
  return { account, asset: created };
}

test("enter, edit and delete a manual price: the detail values it, then at cost again", async ({
  page,
}) => {
  await logIn(page, await newUser("prices"));
  const { account, asset } = await holding(
    page,
    { name: "Esempio Prezzi ETF", asset_type: "ETF", quote_currency: "EUR", quote_unit: "CURRENCY",
      isin: "ZZ0000087039" }, // prettier-ignore
    { quantity: "10", unit_price: "100", gross_amount: "1000.00", total_amount: "1000.00" },
  );
  await page.goto(`/holdings/${asset.id}?account=${account.id}`);
  const value = page.getByRole("region", { name: "Position value" });
  await expect(value).toContainText("€1,000.00");
  await expect(page.getByText("Estimated at cost")).toBeVisible();

  await page.getByRole("link", { name: "Manual prices", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Manual prices", level: 1 })).toBeVisible();
  await expect(page.getByText("No manual prices for this security yet.")).toBeVisible();
  const date = page.getByLabel("Date", { exact: true });
  const today = apiDay(await date.inputValue());
  const day = shown(today, "en");

  // A day after today is refused in the page.
  await date.fill(typedDay(nextDay(today)));
  await page.getByLabel("Price (EUR)").fill("110");
  await page.getByRole("button", { name: "Save price" }).click();
  await expect(page.getByText("The date cannot be in the future.")).toBeVisible();

  await date.fill(typedDay(today));
  await page.getByRole("button", { name: "Save price" }).click();
  await expect(page.getByText(`Price of ${day} saved.`)).toBeVisible();
  const list = page.getByRole("table", { name: "Your manual prices" });
  await expect(list.getByRole("row", { name: new RegExp(day) })).toContainText("€110.00");
  await expect(page.getByText(`Price in use: €110.00 on ${day} (manual)`)).toBeVisible();

  await page.getByRole("link", { name: "← Esempio Prezzi ETF" }).click();
  await expect(value).toContainText("€1,100.00");
  await expect(value).not.toContainText("Price from"); // a manual price names no exchange
  await expect(page.getByText("Manual price", { exact: true })).toBeVisible();
  await expect(page.getByText("Estimated at cost")).toHaveCount(0);
  await page.goto(`/holdings?account=${account.id}`);
  const row = page.getByRole("row", { name: /Esempio Prezzi ETF/ });
  await expect(row).toContainText("€1,100.00");
  await expect(row.getByText("Manual price")).toBeVisible();

  // Edit: the day stays; a new price on that day would replace it, and says so.
  await page.goto(`/holdings/${asset.id}/prices?account=${account.id}`);
  await expect(page.getByText(`You already have a manual price for ${day}: it will be replaced.`)).toBeVisible();
  await page.getByRole("button", { name: `Edit the price of ${day}` }).click();
  await expect(page.getByRole("heading", { name: `Edit the price of ${day}` })).toBeVisible();
  await expect(date).toHaveAttribute("readonly");
  await expect(page.getByLabel("Price (EUR)")).toHaveValue("110");
  await page.getByLabel("Price (EUR)").fill("112.5");
  await page.getByRole("button", { name: "Save price" }).click();
  await expect(page.getByText(`Price in use: €112.50 on ${day} (manual)`)).toBeVisible();
  await expect(list.getByRole("row")).toHaveCount(2); // the header and the one price

  await page.getByRole("button", { name: `Delete the price of ${day}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete price" }).click();
  await expect(page.getByText(`Price of ${day} deleted.`)).toBeVisible();
  await expect(page.getByText("No manual prices for this security yet.")).toBeVisible();
  await page.getByRole("link", { name: "← Esempio Prezzi ETF" }).click();
  await expect(value).toContainText("€1,000.00");
  await expect(page.getByText("Estimated at cost")).toBeVisible();
});

test("a bond with no automatic price: valued at cost, at the manual price, at cost again", async ({
  page,
}) => {
  await logIn(page, await newUser("bond", { language: "it" }));
  const { account, asset } = await holding(
    page,
    { name: "Esempio BTP 2031", asset_type: "BOND", quote_currency: "EUR",
      quote_unit: "PERCENT_OF_NOMINAL" }, // prettier-ignore
    { quantity: "1000", unit_price: "98", gross_amount: "980.00", total_amount: "980.00" },
  );
  const row = page.getByRole("row", { name: /Esempio BTP 2031/ });
  await page.goto(`/holdings?account=${account.id}`);
  await expect(row).toContainText(/980,00\s€/);
  await expect(row.getByText("Stimato al costo")).toBeVisible();

  await page.goto(`/holdings/${asset.id}?account=${account.id}`);
  await page.getByRole("link", { name: "Prezzi a mano", exact: true }).click();
  const day = shown(apiDay(await page.getByLabel("Data", { exact: true }).inputValue()), "it");
  await page.getByLabel("Prezzo (% del nominale)").fill("99,5");
  await page.getByRole("button", { name: "Salva prezzo" }).click();
  await expect(page.getByText(`Prezzo del ${day} salvato.`)).toBeVisible();
  await expect(page.getByRole("table", { name: "I tuoi prezzi a mano" })).toContainText("99,50%");

  await page.goto(`/holdings?account=${account.id}`);
  await expect(row).toContainText(/995,00\s€/); // 1.000 of nominal × 99,50%
  await expect(row.getByText("Stimato al costo")).toHaveCount(0);

  await page.goto(`/holdings/${asset.id}/prices?account=${account.id}`);
  await page.getByRole("button", { name: `Elimina il prezzo del ${day}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Elimina prezzo" }).click();
  await expect(page.getByText(`Prezzo del ${day} eliminato.`)).toBeVisible();
  await page.goto(`/holdings?account=${account.id}`);
  await expect(row).toContainText(/980,00\s€/);
  await expect(row.getByText("Stimato al costo")).toBeVisible();
});
