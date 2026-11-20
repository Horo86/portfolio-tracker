import type {
  HTMLAttributes,
  ReactNode,
  Ref,
  TableHTMLAttributes,
  TdHTMLAttributes,
  ThHTMLAttributes,
} from "react";

/** The class names that are set, joined. */
function classes(...names: (string | false | undefined)[]): string | undefined {
  return names.filter(Boolean).join(" ") || undefined;
}

type TableProps = TableHTMLAttributes<HTMLTableElement> & {
  /** Below 768 px each row becomes a card (D-190): a grid of "label above value" cells. */
  cards?: boolean;
  /** The table itself (a prop in React 19), to move the focus to it. */
  ref?: Ref<HTMLTableElement>;
  children: ReactNode;
};

/**
 * A data table that may become cards on phones (D-190, B5). It is the same native table, with
 * its ARIA roles written out: a table's semantics in the accessibility tree are lost once CSS
 * changes its `display`, so the roles keep rows, headers and cells for screen readers and for
 * every role-based test. On wider screens nothing changes.
 */
export function Table({ cards, className, children, ...rest }: TableProps) {
  return (
    <table role="table" className={classes(className, cards && "cards")} {...rest}>
      {children}
    </table>
  );
}

type GroupProps = HTMLAttributes<HTMLTableSectionElement> & { children: ReactNode };

export function Head({ children, ...rest }: GroupProps) {
  return (
    <thead role="rowgroup" {...rest}>
      {children}
    </thead>
  );
}

export function Body({ children, ...rest }: GroupProps) {
  return (
    <tbody role="rowgroup" {...rest}>
      {children}
    </tbody>
  );
}

/** A table's footer rows (a total, say): on cards it is drawn as the body's rows are. */
export function Foot({ children, ...rest }: GroupProps) {
  return (
    <tfoot role="rowgroup" {...rest}>
      {children}
    </tfoot>
  );
}

export function Tr({ children, ...rest }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr role="row" {...rest}>
      {children}
    </tr>
  );
}

type CellOptions = {
  /** Shown above the value on a card (`data-label`); never read twice by screen readers. */
  label?: string;
  /** Spans both columns of a card. */
  wide?: boolean;
  /** The cell itself (a prop in React 19), to move the focus to it. */
  ref?: Ref<HTMLTableCellElement>;
};

type ThProps = Omit<ThHTMLAttributes<HTMLTableCellElement>, "scope"> &
  CellOptions & {
    scope: "col" | "row";
    /** A column header that stays visible on cards (a "select all" tick, say). */
    keep?: boolean;
  };

/** A column header (`scope="col"`), hidden one by one on cards but still naming the cells,
 * or a row header (`scope="row"`), the card's first cell. */
export function Th({ scope, label, wide, keep, className, children, ...rest }: ThProps) {
  return (
    <th
      scope={scope}
      role={scope === "col" ? "columnheader" : "rowheader"}
      data-label={label}
      className={classes(className, wide && "cell-wide", keep && "cell-keep")}
      {...rest}
    >
      {children}
    </th>
  );
}

type TdProps = TdHTMLAttributes<HTMLTableCellElement> & CellOptions;

export function Td({ label, wide, className, children, ...rest }: TdProps) {
  return (
    <td
      role="cell"
      data-label={label}
      className={classes(className, wide && "cell-wide")}
      {...rest}
    >
      {children}
    </td>
  );
}
