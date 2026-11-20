import { describe, expect, it } from "vitest";

import { engineName } from "./engine";

describe("engineName (one display name everywhere, D-196)", () => {
  it("names the engines of this version", () => {
    expect(engineName("yahoo")).toBe("Yahoo Finance");
    expect(engineName("openfigi")).toBe("OpenFIGI");
    expect(engineName("frankfurter")).toBe("Frankfurter");
  });

  it("shows any other key as it is, never a property of the table", () => {
    expect(engineName("an-engine-of-another-version")).toBe("an-engine-of-another-version");
    expect(engineName("toString")).toBe("toString");
    expect(engineName("")).toBe("");
  });
});
