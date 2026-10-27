import { expect, type Page, test } from "@playwright/test";

import type {
  Account,
  AccountSummary,
  Breakdown,
  PortfolioSummary,
} from "../src/api/types";
import { formatMoney } from "../src/format/decimal";
import { DEMO, logIn, newUser } from "./helpers";

// The dashboard (01, D-153) on the development data, in Italian: every figure on it is the
// server's, formatted, never computed in the browser.

async function get<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`/api/v1${path}`);
  expect(response.ok(), `GET ${path}: ${response.status()}`).toBe(true);
  return (await response.json()) as T;
}

test("the figures are the server's, over all accounts and in one account", async ({ page }) => {
  await logIn(page, DEMO);
  const summary = await get<PortfolioSummary>(page, "/portfolio/summary");
  const breakdown = await get<Breakdown>(page, "/portfolio/breakdown");
  const money = (value: string, currency = "EUR", signed = false) =>
    formatMoney(value, currency, "it", { signed });

  await expect(page.getByText(`Vista: tutti i conti (${summary.account_count})`)).toBeVisible();
  const total = page.getByRole("region", { name: "Valore totale" });
  await expect(total).toContainText(money(summary.total_value));
  await expect(total).toContainText(
    `Titoli ${money(summary.securities_value)} · Cassa ${money(summary.cash)}`,
  );
  await expect(page.getByRole("region", { name: "Capitale investito" })).toContainText(
    money(summary.invested_capital),
  );
  await expect(page.getByRole("region", { name: "Guadagno netto" })).toContainText(
    money(summary.gain_net, "EUR", true),
  );
  const byType = page.getByRole("table", { name: "Per tipo" });
  await expect(byType.getByRole("row")).toHaveCount(breakdown.by_type.length + 1);
  for (const part of breakdown.by_type) await expect(byType).toContainText(money(part.value));
  await expect(page.getByRole("region", { name: "Dividendi e cedole incassati" })).toContainText(
    `Dall'inizio: ${money(breakdown.income_total.gross)} lordi · ${money(breakdown.income_total.net)} netti`,
  );

  // One account: its own currency and its own figures.
  const accounts = await get<Account[]>(page, "/accounts");
  const foreign = accounts.find((a) => a.currency === "USD") as Account;
  const one = await get<AccountSummary>(page, `/accounts/${foreign.id}/summary`);
  await page.getByLabel("Conto in vista").selectOption({ label: foreign.name });
  await expect(page.getByText(`Vista: ${foreign.name} · valori in USD`)).toBeVisible();
  await expect(total).toContainText(money(one.total_value, "USD"));
  await expect(page.getByRole("region", { name: "Guadagno netto" })).toContainText(
    money(one.gain_net, "USD", true),
  );
  await expect(page.getByText(/convertiti al cambio più recente/)).toHaveCount(0);
});

test("before the first account the dashboard leads to Conti", async ({ page }) => {
  await logIn(page, await newUser("dash", { language: "it" }));
  await expect(page.getByText("Per iniziare crea il primo conto.")).toBeVisible();
  await page.getByRole("link", { name: "Vai ai Conti" }).click();
  await expect(page.getByRole("heading", { name: "Conti", level: 1 })).toBeVisible();
});

test("the charts are drawn beside their tables, hidden from readers", async ({ page }) => {
  await logIn(page, DEMO);
  const allocation = page.getByRole("region", { name: "Come sono distribuiti i soldi" });
  const income = page.getByRole("region", { name: "Dividendi e cedole incassati" });
  for (const chart of [
    allocation.locator(".chart-donut"),
    allocation.locator(".chart-bar"),
    income.locator(".chart-months"),
  ]) {
    await expect(chart.locator("svg")).toBeVisible();
    await expect(chart).toHaveAttribute("aria-hidden", "true");
    // Nothing in a chart takes the focus: the tables carry the values.
    await expect(chart.locator("a, [tabindex]")).toHaveCount(0);
  }
  // One slice per row of the table, in its colors (the swatches are the legend).
  const rows = await page.getByRole("table", { name: "Per tipo" }).locator("tbody tr").count();
  await expect(allocation.locator(".chart-donut svg path[fill^='#']")).toHaveCount(rows);
});
