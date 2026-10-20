import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { routes } from "../router";
import { page, RouteError } from "./RouteError";

function Boom({ error }: { error: Error }): never {
  throw error;
}

function showError(error: Error) {
  const router = createMemoryRouter([
    { path: "/", element: <Boom error={error} />, errorElement: <RouteError /> },
  ]);
  render(<RouterProvider router={router} />);
}

const STALE = new TypeError(
  "Failed to fetch dynamically imported module: http://localhost/assets/Settings-abc.js",
);

describe("error screen", () => {
  let reload: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    reload = vi.spyOn(page, "reload").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined); // React reports the error
  });
  afterEach(() => vi.restoreAllMocks());

  it("is the root route's, instead of React Router's screen with the stack", () => {
    expect(routes[0].errorElement).toBeDefined();
  });

  it("shows translated text only, never the message or the stack", async () => {
    showError(new Error("secret detail at render (file.ts:12)"));
    expect(await screen.findByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/secret detail|file\.ts/);
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the dashboard" })).toHaveAttribute("href", "/");
    expect(reload).not.toHaveBeenCalled();
  });

  it("reloads once for a page file of an older build, never in a loop", async () => {
    showError(STALE);
    expect(
      await screen.findByRole("heading", { name: "A new version is available" }),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Settings-abc/);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem("pt.reloaded-for-update")).not.toBeNull();

    // After that reload the file is still missing: the screen stays, no second reload.
    showError(STALE);
    await screen.findAllByRole("heading", { name: "A new version is available" });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("never reloads by itself without sessionStorage", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    showError(STALE);
    await screen.findByRole("heading", { name: "A new version is available" });
    expect(reload).not.toHaveBeenCalled();
  });
});
