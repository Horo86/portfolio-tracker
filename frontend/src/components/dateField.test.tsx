import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import i18n from "../i18n";
import { DateField } from "./DateField";

type HarnessProps = {
  initial?: string;
  error?: string;
  max?: string;
  readOnly?: boolean;
};

/** A form holding the field's value, as the pages do; it shows what the field hands out. */
function Harness({ initial = "", error, max, readOnly }: HarnessProps) {
  const [value, setValue] = useState(initial);
  return (
    <form onSubmit={(event) => event.preventDefault()}>
      <DateField
        label="Date"
        value={value}
        onChange={setValue}
        error={error}
        max={max}
        readOnly={readOnly}
      />
      <output data-testid="value">{value}</output>
      <button type="button" onClick={() => setValue("2026-01-02")}>
        Set from outside
      </button>
    </form>
  );
}

const field = () => screen.getByLabelText("Date");
const handedOut = () => screen.getByTestId("value").textContent;
const type = (text: string) => fireEvent.change(field(), { target: { value: text } });

afterEach(() => {
  delete (HTMLInputElement.prototype as { showPicker?: unknown }).showPicker;
});

describe("DateField (M5 slice 6)", () => {
  it("shows an API day as dd/mm/yyyy and hands a typed day out as YYYY-MM-DD", () => {
    render(<Harness initial="2026-10-28" />);
    expect(field()).toHaveValue("28/10/2026");
    expect(field()).toHaveAttribute("inputmode", "numeric");
    type("5.3.2026");
    expect(handedOut()).toBe("2026-03-05");
    type("");
    expect(handedOut()).toBe("");
  });

  it("says its format with the field, in English", () => {
    render(<Harness />);
    expect(field()).toHaveAttribute("placeholder", "dd/mm/yyyy");
    expect(field()).toHaveAccessibleDescription("Format dd/mm/yyyy");
  });

  it("says its format with the field, in Italian", async () => {
    await i18n.changeLanguage("it");
    render(<Harness initial="2026-10-28" />);
    expect(field()).toHaveValue("28/10/2026");
    expect(field()).toHaveAttribute("placeholder", "gg/mm/aaaa");
    expect(field()).toHaveAccessibleDescription("Formato gg/mm/aaaa");
  });

  it("tells a wrong format once the field is left, announced and linked to it", () => {
    render(<Harness />);
    type("28/10/26");
    expect(handedOut()).toBe("typed:28/10/26"); // not a day: every page's check refuses it
    expect(field()).not.toHaveAttribute("aria-invalid");
    fireEvent.blur(field());
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(field()).toHaveAccessibleDescription("Format dd/mm/yyyy Enter the date as dd/mm/yyyy.");
    expect(screen.getByText("Enter the date as dd/mm/yyyy.").closest("[aria-live]")).toHaveAttribute(
      "aria-live",
      "polite",
    );
    type("28/10/2026");
    expect(field()).not.toHaveAttribute("aria-invalid");
    expect(handedOut()).toBe("2026-10-28");
  });

  it("tells a day that does not exist, in either language", async () => {
    const { unmount } = render(<Harness />);
    type("31/02/2026");
    fireEvent.blur(field());
    expect(screen.getByText("This date does not exist.")).toBeInTheDocument();
    unmount();

    await i18n.changeLanguage("it");
    render(<Harness />);
    type("31/02/2026");
    fireEvent.blur(field());
    expect(screen.getByText("Questa data non esiste.")).toBeInTheDocument();
    type("1/2/26"); // told again once left: not while typing
    expect(screen.queryByText("Questa data non esiste.")).not.toBeInTheDocument();
    fireEvent.blur(field());
    expect(screen.getByText("Scrivi la data come gg/mm/aaaa.")).toBeInTheDocument();
  });

  it("after Save its error is read with the field, not announced a second time", () => {
    render(<Harness error="Required." />);
    type("31/02/2026"); // the page reports a problem and moves the focus here: read once
    const message = screen.getByText("This date does not exist.");
    expect(message.closest("[aria-live]")).toBeNull();
    expect(field()).toHaveAccessibleDescription("Format dd/mm/yyyy This date does not exist.");
  });

  it("announces the page's error when asked: no focus moves to it (the filters)", () => {
    render(<DateField label="Date" value="2026-04-02" onChange={() => {}} error="After To." announce />);
    expect(screen.getByText("After To.").closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
  });

  it("puts its own error before the page's, and shows the page's for a valid or empty day", () => {
    const { rerender } = render(<Harness error="Required." />);
    expect(screen.getByText("Required.")).toBeInTheDocument();
    type("31/02/2026"); // the page reports a problem (a save was tried): told at once
    expect(screen.getByText("This date does not exist.")).toBeInTheDocument();
    expect(screen.queryByText("Required.")).not.toBeInTheDocument();
    rerender(<Harness error="Not after today." />);
    type("01/01/2099");
    expect(screen.getByText("Not after today.")).toBeInTheDocument();
  });

  it("shows a new value from outside in place of what was typed", () => {
    render(<Harness />);
    type("12/12/20");
    fireEvent.click(screen.getByRole("button", { name: "Set from outside" }));
    expect(field()).toHaveValue("02/01/2026");
    expect(handedOut()).toBe("2026-01-02");
  });

  it("keeps what is being typed when a value it handed out arrives late (the filters' URL)", () => {
    const handed: string[] = [];
    const lagging = (value: string) => (
      <DateField label="Date" value={value} onChange={(v) => handed.push(v)} />
    );
    const { rerender } = render(lagging("2025-01-01"));
    type("");
    type("1/1/2");
    rerender(lagging("")); // the emptied filter reaches the page only now
    expect(field()).toHaveValue("1/1/2");
    rerender(lagging("2026-03-01")); // a value from outside still replaces it
    expect(field()).toHaveValue("01/03/2026");
    expect(handed).toEqual(["", "typed:1/1/2"]);
  });

  it("opens the browser's calendar on the field's day, within its limits", () => {
    const showPicker = vi.fn();
    (HTMLInputElement.prototype as { showPicker?: unknown }).showPicker = showPicker;
    const { container } = render(<Harness initial="2026-10-28" max="2026-10-30" />);
    fireEvent.click(screen.getByRole("button", { name: "Pick from the calendar: Date" }));
    const picker = container.querySelector<HTMLInputElement>('input[type="date"]');
    expect(showPicker).toHaveBeenCalledTimes(1);
    expect(picker).toHaveValue("2026-10-28");
    expect(picker).toHaveAttribute("max", "2026-10-30");
    expect(picker).toHaveAttribute("tabindex", "-1");
    fireEvent.change(picker!, { target: { value: "2026-02-03" } });
    expect(field()).toHaveValue("03/02/2026");
    expect(handedOut()).toBe("2026-02-03");
  });

  it("shows no calendar button where the browser cannot open its calendar", () => {
    (HTMLInputElement.prototype as { showPicker?: unknown }).showPicker = undefined;
    const { container } = render(<Harness initial="2026-10-28" />);
    expect(screen.queryByRole("button", { name: /calendar/ })).not.toBeInTheDocument();
    expect(container.querySelector('input[type="date"]')).toBeNull();
    type("5/3/2026"); // the text field alone does it all
    expect(handedOut()).toBe("2026-03-05");
  });

  it("hands out a text typed in the API's order as not a day, never as that day", () => {
    render(<Harness />);
    type("2026-10-28");
    expect(handedOut()).toBe("typed:2026-10-28");
    fireEvent.blur(field());
    expect(screen.getByText("Enter the date as dd/mm/yyyy.")).toBeInTheDocument();
  });

  it("has no calendar when it cannot be changed", () => {
    render(<Harness initial="2026-10-28" readOnly />);
    expect(field()).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: /calendar/ })).not.toBeInTheDocument();
    type("01/01/2026"); // a change that reaches it anyway is ignored
    expect(field()).toHaveValue("28/10/2026");
    expect(handedOut()).toBe("2026-10-28");
  });
});
