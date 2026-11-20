import { render, screen, within } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import { Body, Foot, Head, Table, Td, Th, Tr } from "./Table";

// Invented rows only.
function Example({ cards }: { cards?: boolean }) {
  return (
    <>
      <h2 id="people">People</h2>
      <Table cards={cards} className="data" aria-labelledby="people">
        <Head>
          <Tr>
            <Th scope="col" keep>
              Pick
            </Th>
            <Th scope="col">Name</Th>
            <Th scope="col">E-mail</Th>
            <Th scope="col">Role</Th>
          </Tr>
        </Head>
        <Body>
          <Tr>
            <Td>
              <input type="checkbox" aria-label="Pick anna" />
            </Td>
            <Th scope="row" wide>
              anna
            </Th>
            <Td label="E-mail" wide className="user-email">
              anna@example.org
            </Td>
            <Td label="Role">Investor</Td>
          </Tr>
        </Body>
      </Table>
    </>
  );
}

describe("Table (D-190): one table that may become cards on phones", () => {
  it("writes the ARIA roles out, so CSS cannot take the table's semantics away", () => {
    const { container } = render(<Example cards />);
    const table = screen.getByRole("table", { name: "People" });
    expect(table).toHaveAttribute("role", "table");
    expect(within(table).getAllByRole("rowgroup")).toHaveLength(2);
    expect(container.querySelector("thead")).toHaveAttribute("role", "rowgroup");
    expect(container.querySelector("tbody")).toHaveAttribute("role", "rowgroup");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    for (const row of container.querySelectorAll("tr")) expect(row).toHaveAttribute("role", "row");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Pick",
      "Name",
      "E-mail",
      "Role",
    ]);
    const name = within(table).getByRole("rowheader", { name: "anna" });
    expect(name).toHaveAttribute("scope", "row");
    expect(within(table).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "",
      "anna@example.org",
      "Investor",
    ]);
  });

  it("puts the label on the cell as data-label, never as text read twice", () => {
    render(<Example cards />);
    const email = screen.getByRole("cell", { name: "anna@example.org" });
    expect(email).toHaveAttribute("data-label", "E-mail");
    expect(email).toHaveClass("user-email", "cell-wide");
    expect(screen.getByRole("cell", { name: "Investor" })).toHaveAttribute("data-label", "Role");
    expect(screen.getByRole("cell", { name: "Investor" })).not.toHaveClass("cell-wide");
    expect(screen.getByRole("rowheader", { name: "anna" })).toHaveClass("cell-wide");
    expect(screen.getByRole("rowheader", { name: "anna" })).not.toHaveAttribute("data-label");
  });

  it("marks a header kept on cards, and the table only when asked", () => {
    const { rerender } = render(<Example cards />);
    expect(screen.getByRole("table")).toHaveClass("data", "cards");
    expect(screen.getByRole("columnheader", { name: "Pick" })).toHaveClass("cell-keep");
    expect(screen.getByRole("columnheader", { name: "Name" })).not.toHaveAttribute("class");
    rerender(<Example />);
    expect(screen.getByRole("table")).toHaveAttribute("class", "data");
  });

  it("gives a footer the rowgroup role, so a total row keeps its place in the table", () => {
    const { container } = render(
      <Table className="data" aria-label="Totals">
        <Body>
          <Tr>
            <Th scope="row">One</Th>
            <Td>1</Td>
          </Tr>
        </Body>
        <Foot>
          <Tr>
            <Th scope="row">Total</Th>
            <Td>1</Td>
          </Tr>
        </Foot>
      </Table>,
    );
    const table = screen.getByRole("table", { name: "Totals" });
    expect(within(table).getAllByRole("rowgroup")).toHaveLength(2);
    expect(container.querySelector("tfoot")).toHaveAttribute("role", "rowgroup");
    expect(within(table).getByRole("rowheader", { name: "Total" })).toBeInTheDocument();
  });

  it("hands the table itself to a ref, so a page can move the focus to it", () => {
    const ref = createRef<HTMLTableElement>();
    render(
      <Table className="data" aria-label="Prices" ref={ref} tabIndex={-1}>
        <Body>
          <Tr>
            <Td>1</Td>
          </Tr>
        </Body>
      </Table>,
    );
    expect(ref.current).toBe(screen.getByRole("table", { name: "Prices" }));
  });
});
