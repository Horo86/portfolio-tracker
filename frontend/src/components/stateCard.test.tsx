import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { StateCard } from "./StateCard";
import { LoadError } from "./Status";

describe("the card of an empty or error state (mockup 19)", () => {
  it("an icon hidden from screen readers, the heading at the level given, text and actions", () => {
    const { container } = render(
      <StateCard icon="search" heading="No results" level={3} actions={<button>Clear</button>}>
        <p>Nothing matches.</p>
      </StateCard>,
    );
    expect(screen.getByRole("heading", { name: "No results", level: 3 })).toBeInTheDocument();
    const icon = container.querySelector("svg")!;
    expect(icon.closest("[aria-hidden=true]")).not.toBeNull();
    expect(icon).toHaveAttribute("focusable", "false");
    expect(screen.getByText("Nothing matches.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("a level 2 heading by default, none when the page's own heading says it", () => {
    const { rerender } = render(<StateCard icon="account" heading="Ready" />);
    expect(screen.getByRole("heading", { name: "Ready", level: 2 })).toBeInTheDocument();
    rerender(<StateCard icon="account" />);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("level 1 is the page's title", () => {
    render(
      <MemoryRouter>
        <StateCard icon="notFound" heading="Page not found" level={1} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Page not found", level: 1 })).toBeInTheDocument();
    expect(document.title).toBe("Page not found · Portfolio Tracker");
  });
});

describe("a load that failed (mockup 19, generic error)", () => {
  it("heading, what happened, the logs hint, the code and Try again", () => {
    const retry = vi.fn();
    render(<LoadError error={new ApiError(503, "SERVER")} onRetry={retry} />);
    const alert = screen.getByRole("alert");
    expect(screen.getByRole("heading", { name: "Something went wrong", level: 2 })).toBeInTheDocument();
    expect(alert).toHaveTextContent(
      "The server had a problem. Try again in a moment. If it happens again, check the container's logs.",
    );
    expect(alert).toHaveTextContent("Code: SERVER");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("a code without a text of its own: the data could not be loaded, the code said once", () => {
    render(<LoadError error={new ApiError(500, "INTERNAL_ERROR")} onRetry={() => {}} level={3} />);
    const alert = screen.getByRole("alert");
    expect(screen.getByRole("heading", { name: "Something went wrong", level: 3 })).toBeInTheDocument();
    expect(alert).toHaveTextContent("The data could not be loaded.");
    expect(alert).toHaveTextContent("Code: INTERNAL_ERROR");
    expect(alert.textContent!.match(/INTERNAL_ERROR/g)).toHaveLength(1);
  });

  it("not an answer of the server: no code", () => {
    render(<LoadError error={new Error("secret")} onRetry={() => {}} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The data could not be loaded.");
    expect(alert).not.toHaveTextContent(/Code|secret/);
  });
});
