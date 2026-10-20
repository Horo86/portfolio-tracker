import { describe, expect, it } from "vitest";

import { deviceOf } from "./device";

describe("deviceOf", () => {
  it("names the common browsers and systems", () => {
    expect(
      deviceOf("Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0"),
    ).toEqual({ browser: "Firefox", os: "Linux" });
    expect(
      deviceOf(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
          "Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
      ),
    ).toEqual({ browser: "Edge", os: "Windows" });
    expect(
      deviceOf(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 " +
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
      ),
    ).toEqual({ browser: "Safari", os: "iOS" });
    expect(
      deviceOf(
        "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) " +
          "Chrome/141.0.0.0 Mobile Safari/537.36",
      ),
    ).toEqual({ browser: "Chrome", os: "Android" });
  });

  it("gives up on anything else", () => {
    expect(deviceOf("curl/8.10.1")).toBeNull();
  });
});
