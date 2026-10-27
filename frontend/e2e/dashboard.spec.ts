import { expect, type Page, test } from "@playwright/test";

import type {
  Account,
  AccountSummary,
  Breakdown,
  PortfolioSummary,
} from "../src/api/types";
import { formatMoney } from "../src/format/decimal";
import { apiPost, DEMO, logIn, newUser } from "./helpers";

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
  // The month bars only when a month of the twelve has income (the dates of the development
  // data are fixed, so that depends on today).
  const breakdown = await get<Breakdown>(page, "/portfolio/breakdown");
  const months = breakdown.income_by_month.some((m) => !/^0(\.0+)?$/.test(m.gross));
  await expect(income.locator(".chart-months")).toHaveCount(months ? 1 : 0);
  for (const chart of [
    allocation.locator(".chart-donut"),
    allocation.locator(".chart-bar"),
    ...(months ? [income.locator(".chart-months")] : []),
  ]) {
    await expect(chart.locator("svg")).toBeVisible();
    await expect(chart).toHaveAttribute("aria-hidden", "true");
    // Nothing in a chart takes the focus: the tables carry the values.
    await expect(chart.locator("a, [tabindex]")).toHaveCount(0);
  }
  // One slice per row of the table, in its colors (the swatches are the legend).
  const rows = await page.getByRole("table", { name: "Per tipo" }).locator("tbody tr").count();
  await expect(allocation.locator(".chart-donut svg path[fill^='#']")).toHaveCount(rows);
  // A pattern besides each color.
  expect(await allocation.locator(".chart-donut svg path[fill^='url(#']").count()).toBeGreaterThan(0);

  // The narrowest window with two columns of cards: the type table stays inside its card.
  await page.setViewportSize({ width: 1180, height: 900 });
  const card = await allocation.boundingBox();
  const table = await page.getByRole("table", { name: "Per tipo" }).boundingBox();
  expect(table && card && table.x + table.width <= card.x + card.width).toBe(true);
});

test("a dividend of this month draws its bar", async ({ page }) => {
  await logIn(page, await newUser("bars", { language: "it" }));
  type Created = { id: string };
  const account = await apiPost<Created>(page, "/accounts", { name: "Conto", currency: "EUR" });
  const etf = await apiPost<Created>(page, "/assets", {
    name: "Esempio Mondo ETF",
    asset_type: "ETF",
    quote_currency: "EUR",
    quote_unit: "CURRENCY",
  });
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  const trades = [
    { type: "DEPOSIT", trade_date: yesterday, total_amount: "1000.00" },
    {
      type: "BUY", trade_date: yesterday, asset_id: etf.id, quantity: "5",
      unit_price: "100.00", gross_amount: "500.00", total_amount: "500.00",
    },
    {
      type: "DIVIDEND", trade_date: yesterday, asset_id: etf.id, gross_amount: "25.00",
      taxes: "6.50", total_amount: "18.50",
    },
  ]; // prettier-ignore
  for (const tx of trades) await apiPost(page, "/transactions", { account_id: account.id, ...tx });
  await page.goto("/");
  const income = page.getByRole("region", { name: "Dividendi e cedole incassati" });
  await expect(income.locator(".chart-months svg")).toBeVisible();
  // Twelve months, one bar with a height: the others have nothing.
  const bars = income.locator(".chart-months svg path[fill^='#']");
  const tall = await bars.evaluateAll(
    (paths) => paths.filter((p) => (p as SVGGraphicsElement).getBBox().height > 1).length,
  );
  expect(tall).toBe(1);
  await expect(income).toContainText("Dall'inizio: 25,00\u00a0€ lordi · 18,50\u00a0€ netti");
});
