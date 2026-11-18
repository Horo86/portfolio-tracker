import { describe, expect, it } from "vitest";

import main from "../main.tsx?raw";
import styles from "../styles.css?raw";
import { COMPACT_QUERY } from "../useCompact";
import css from "./responsive.css?raw";

const COMPACT = "@media (max-width: 1023.98px)";
const PHONE = "@media (max-width: 767.98px)";

/** The top-level blocks of a stylesheet: what stands before each outermost `{`. */
function topLevel(text: string): string[] {
  const plain = text.replace(/\/\*[\s\S]*?\*\//g, "");
  const heads: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < plain.length; i++) {
    const c = plain[i];
    if (c === "{") {
      if (depth === 0) heads.push(plain.slice(start, i).trim());
      depth++;
    } else if (c === "}") {
      depth--;
      if (depth === 0) start = i + 1;
    } else if (c === ";" && depth === 0) {
      heads.push(plain.slice(start, i).trim()); // an at-rule without a block, e.g. @import
      start = i + 1;
    }
  }
  expect(depth, "balanced braces").toBe(0);
  expect(plain.slice(start).trim(), "nothing after the last block").toBe("");
  return heads;
}

describe("responsive.css (D-189)", () => {
  it("keeps every rule inside one of the two queries, so desktop cannot change", () => {
    const heads = topLevel(css);
    expect(heads.length).toBeGreaterThan(0);
    for (const head of heads) expect([COMPACT, PHONE]).toContain(head);
  });

  it("uses the same breakpoint as useCompact", () => {
    expect(COMPACT).toBe(`@media ${COMPACT_QUERY}`);
  });

  it("is imported by main.tsx after styles.css, and not by styles.css", () => {
    const base = main.indexOf('import "./styles.css";');
    const responsive = main.indexOf('import "./styles/responsive.css";');
    expect(base).toBeGreaterThanOrEqual(0);
    expect(responsive).toBeGreaterThan(base);
    expect(styles).not.toContain("responsive.css");
  });
});
