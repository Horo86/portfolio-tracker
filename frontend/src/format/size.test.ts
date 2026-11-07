import { describe, expect, it } from "vitest";

import { formatSize } from "./size";

describe("formatSize", () => {
  it("writes bytes, kB, MB and GB in the app's language", () => {
    expect(formatSize(512, "en")).toBe("512 B");
    expect(formatSize(6602, "en")).toBe("6.4 kB");
    expect(formatSize(6602, "it")).toBe("6,4 kB");
    expect(formatSize(278528, "en")).toBe("272 kB");
    expect(formatSize(3 * 1024 ** 2 + 512 * 1024, "en")).toBe("3.5 MB");
    expect(formatSize(5 * 1024 ** 4, "en")).toBe("5,120 GB");
  });
});
