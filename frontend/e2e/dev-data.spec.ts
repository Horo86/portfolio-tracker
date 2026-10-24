import { expect, test } from "@playwright/test";

import { DEMO, logIn } from "./helpers";

// The development data (backend/scripts/dev_data.py) as the demo user sees it, in Italian.

test("the seeded dollar account is valued in euros at the invented rate", async ({ page }) => {
  // dev_data.py stores 1.25 USD per EUR for yesterday: 10,249.00 USD = 8,199.20 EUR, with no
  // stale-rate notice on fresh data.
  await logIn(page, DEMO);
  await page.goto("/accounts");
  const card = page.getByRole("region", { name: "Conto estero" });
  await expect(card).toContainText(
    /Controvalore in EUR all'ultimo cambio disponibile: 8\.199,20\s€/,
  );
  await expect(card).not.toContainText("non aggiornato");
});
