import { expect, test } from "./fixtures";

import { expectAccessible, logIn, newUser } from "./helpers";

// The third-party licences on About (D-167), on the seeded server. It serves frontend/dist, where
// the build wrote the web app's list; the server's list is written only by the image build, so
// here About says it comes with the built app (the CI image job checks the server's list).

test("About lists the web app's licences behind 'Show the list' and links to their texts", async ({
  page,
  request,
}) => {
  const user = await newUser("licencereader");
  await logIn(page, user);
  await page.goto("/about");
  const section = page.getByRole("region", { name: "Third-party licences" });
  await expect(section).toBeVisible();
  await expect(section).toContainText(
    "The list comes with the built app; this is a development run.",
  );

  const show = section.getByText(/^Show the list \(\d+ packages\)$/);
  await expect(show).toHaveCount(1);
  const webApp = section.getByRole("table", { name: "Web app" });
  await expect(webApp).toBeHidden();
  await show.click();
  await expect(webApp).toBeVisible();
  await expect(webApp.getByRole("row", { name: /^react \S+ MIT$/ })).toBeVisible();
  await expect(webApp.getByRole("row", { name: /^echarts \S+ Apache-2\.0$/ })).toBeVisible();

  const link = section.getByRole("link", { name: "Read the full licence texts" });
  await expect(link).toHaveAttribute("href", "/licenses/THIRD-PARTY.txt");
  const texts = await request.get("/licenses/THIRD-PARTY.txt");
  expect(texts.status()).toBe(200);
  expect(await texts.text()).toContain("Licensed under the Apache License");
  expect((await request.get("/licenses/backend.json")).status()).toBe(404);

  await expectAccessible(page, "About with the web app's licences open");
});
