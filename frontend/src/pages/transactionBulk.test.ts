import { describe, expect, it } from "vitest";

import type { Transaction } from "../api/types";
import { bulkEdits } from "./TransactionBulk";

// Invented transactions only: just the fields a bulk edit looks at.
const tx = (id: string, extra: Partial<Transaction> = {}) =>
  ({
    id,
    type: "BUY",
    account_id: "acc-a",
    affects_cash: true,
    notes: null,
    ...extra,
  }) as Transaction;

describe("bulkEdits", () => {
  it("sends only what changes, and the cash flag only to the types that have it", () => {
    const txs = [
      tx("buy"),
      tx("moved", { account_id: "acc-b", affects_cash: false }),
      tx("deposit", { type: "DEPOSIT" }),
      tx("split", { type: "STOCK_SPLIT", notes: "Già" }),
    ];
    expect(bulkEdits(txs, { account: "acc-b", noCash: "yes", notes: "  Già " })).toEqual([
      { id: "buy", account_id: "acc-b", affects_cash: false, notes: "Già" },
      { id: "moved", notes: "Già" },
      { id: "deposit", account_id: "acc-b", notes: "Già" },
      { id: "split", account_id: "acc-b" },
    ]);
  });

  it("leaves out a transaction with nothing to change; blank notes change nothing", () => {
    const txs = [tx("buy"), tx("dividend", { type: "DIVIDEND", affects_cash: false })];
    expect(bulkEdits(txs, { account: "", noCash: "no", notes: "   " })).toEqual([
      { id: "dividend", affects_cash: true },
    ]);
    expect(bulkEdits(txs, { account: "acc-a", noCash: "", notes: "" })).toEqual([]);
  });
});
