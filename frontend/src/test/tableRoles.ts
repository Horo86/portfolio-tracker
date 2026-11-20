import { expect } from "vitest";

/** A table drawn with components/Table.tsx: every role written out (D-190), so a change of
 * `display` on phones cannot take its semantics away. */
export function expectRolesWritten(table: HTMLElement): void {
  expect(table).toHaveAttribute("role", "table");
  for (const group of table.querySelectorAll("thead, tbody, tfoot")) {
    expect(group).toHaveAttribute("role", "rowgroup");
  }
  for (const row of table.querySelectorAll("tr")) expect(row).toHaveAttribute("role", "row");
  for (const header of table.querySelectorAll("th")) {
    expect(header.getAttribute("role")).toBe(header.getAttribute("scope") === "row" ? "rowheader" : "columnheader");
  }
  for (const cell of table.querySelectorAll("td")) expect(cell).toHaveAttribute("role", "cell");
}
