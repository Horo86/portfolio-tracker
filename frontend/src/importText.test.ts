import type { TFunction } from "i18next";
import { describe, expect, it } from "vitest";

import i18n from "./i18n";
import { blockingText, lineText, noticeText, rowsText, type TextContext } from "./importText";

// Invented names, rows and figures only.
function context(language: "it" | "en"): TextContext {
  return {
    t: i18n.getFixedT(language) as TFunction,
    locale: language,
    currency: "EUR",
    assetName: (key) => ({ "isin:ZZ0000002012": "Nuovo Esempio ETF" })[key] ?? key,
    accountName: (key) => (key === "" ? "Conto Esempio" : key),
    rowsOf: (key) => ({ "line:5": [5], "order:T1": [31, 30] })[key] ?? [],
  };
}

const en = context("en");
const it_ = context("it");

describe("rowsText", () => {
  it("names one row or lists several in order", () => {
    expect(rowsText(en.t, [13])).toBe("row 13");
    expect(rowsText(it_.t, [31, 30])).toBe("righe 30, 31");
  });
});

describe("lineText", () => {
  it("formats a withholding amount for the language", () => {
    const reason = { code: "WITHHOLDING_PAIRED", params: { amount: "6.25" } };
    expect(lineText(it_, reason)).toBe("Ritenuta di 6,25\u00a0€ inclusa.");
    expect(lineText(en, reason)).toBe("Withholding of €6.25 included.");
  });

  it("translates the problem of a cell and of a CSV field", () => {
    expect(
      lineText(en, {
        code: "CELL_PROBLEM",
        params: { column: "Isin", problem: "NOT_AN_ISIN", line: "14" },
      }),
    ).toBe("Row 14, column «Isin»: not a valid ISIN.");
    expect(
      lineText(it_, { code: "FIELD_INVALID", params: { field: "type", problem: "UNKNOWN_TYPE" } }),
    ).toBe("«type»: tipo di transazione sconosciuto.");
  });

  it("uses the field issue texts for a field rule, or a general text without a rule", () => {
    expect(
      lineText(en, { code: "FIELD_RULE", params: { field: "quantity", rule: "must_be_positive" } }),
    ).toBe("«quantity»: Enter a number greater than zero.");
    expect(lineText(en, { code: "FIELD_RULE", params: { field: "total_amount" } })).toBe(
      "«total_amount»: this value is not valid for this kind of transaction.",
    );
  });

  it("says why a line is already present and what an unconfirmed type is read as", () => {
    expect(lineText(it_, { code: "ALREADY_PRESENT", params: { match: "id" } })).toBe(
      "Già importato (stesso id della transazione).",
    );
    expect(
      lineText(en, {
        code: "UNCONFIRMED_MOVEMENT_TYPE",
        params: { original_type: "Movimento Esempio", mapped_as: "TAX_CREDIT" },
      }),
    ).toBe(
      "The broker has not confirmed what «Movimento Esempio» means; it is read as a tax credit. Tick it only if that is right.",
    );
  });

  it("names an unknown code and keeps an unknown parameter value as written", () => {
    expect(lineText(en, { code: "SOMETHING_NEW", params: {} })).toBe(
      "Not recognized by this version of the app (code SOMETHING_NEW).",
    );
    expect(
      lineText(en, { code: "FIELD_INVALID", params: { field: "notes", problem: "NEW_PROBLEM" } }),
    ).toBe("«notes»: NEW_PROBLEM.");
  });

  it("lists the other rows of a duplicated id", () => {
    expect(lineText(en, { code: "DUPLICATE_ID", params: { lines: "3,7" } })).toBe(
      "The same id is also on rows 3, 7.",
    );
  });
});

describe("noticeText", () => {
  it("names the account and formats the dates of a manual overlap", () => {
    const reason = {
      code: "MANUAL_OVERLAP",
      params: { account: "", count: "8", first_date: "2025-01-02", last_date: "2025-08-01" },
    };
    expect(noticeText(en, reason)).toBe(
      "Conto Esempio already has transactions entered without a broker reference between Jan 2, 2025 and Aug 1, 2025 (8). If some of them are in this file too, they would be counted twice: check before confirming.",
    );
  });

  it("names the security and the field that differs", () => {
    expect(
      noticeText(it_, {
        code: "ASSET_DEFINITION_DIFFERS",
        params: { asset: "isin:ZZ0000002012", field: "quote_unit" },
      }),
    ).toBe("Nuovo Esempio ETF: l'unità del prezzo nel file è diverso dal tuo titolo; resta il tuo.");
  });
});

describe("blockingText", () => {
  it("names the security that needs a type", () => {
    expect(
      blockingText(en, { code: "ASSET_TYPE_REQUIRED", params: { asset: "isin:ZZ0000002012" } }),
    ).toBe("Choose the type of Nuovo Esempio ETF.");
  });

  it("explains a quantity conflict with its row and the quantity it would reach", () => {
    const reason = {
      code: "QUANTITY_CONFLICT",
      params: {
        account: "Conto CSV",
        line: "line:5",
        quantity: "-20",
        reason: "NEGATIVE_QUANTITY",
        transaction_id: "",
      },
    };
    expect(blockingText(it_, reason)).toBe(
      "Conto CSV: l'importazione porterebbe una quantità a -20 (riga 5): non puoi vendere più di quanto possiedi. Deseleziona quella riga, oppure importa anche gli acquisti precedenti.",
    );
    const existing = { ...reason, params: { ...reason.params, line: "", reason: "SPLIT_TO_ZERO" } };
    expect(blockingText(en, existing)).toBe(
      "Conto CSV: a split would bring a quantity to zero (a transaction already in the account).",
    );
  });

  it("uses the field issue text for an invalid new security", () => {
    expect(
      blockingText(en, {
        code: "ASSET_INVALID",
        params: { asset: "isin:ZZ0000002012", field: "quote_unit", rule: "must_match_asset_type" },
      }),
    ).toBe("Nuovo Esempio ETF: Bonds, and only bonds, are priced as a % of nominal.");
  });
});
