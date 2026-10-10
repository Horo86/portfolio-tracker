import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import i18n from "./i18n";

describe("App", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the translated title and the health status from the API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ status: "ok", version: "0.1.0", database: "ok" })),
    );
    await i18n.changeLanguage("it");

    render(<App />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Portfolio Tracker");
    expect(
      await screen.findByText("Server raggiungibile (versione 0.1.0), database OK."),
    ).toBeInTheDocument();
  });
});
