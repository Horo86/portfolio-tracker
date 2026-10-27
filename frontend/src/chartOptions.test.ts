import { describe, expect, it } from "vitest";

import { type ChartTheme, monthBars, shareBar, shareDonut, swatch } from "./chartOptions";

const THEME: ChartTheme = {
  colors: ["#111111", "#222222", "#333333", "#444444"],
  surface: "#ffffff",
  muted: "#555555",
};
const parts = (...shares: string[]) => shares.map((share, i) => ({ key: `K${i}`, share }));

describe("shareDonut (allocation by type)", () => {
  it("draws each share as a slice in the theme's colors, in the order of the table", () => {
    const option = shareDonut(parts("0.6", "0.25", "0.15"), THEME, true);
    const [series] = option.series;
    expect(series.type).toBe("pie");
    expect(series.silent).toBe(true); // nothing to hover or focus: the table has the values
    expect(series.label.show).toBe(false);
    expect(series.data.map((d) => [d.value, d.itemStyle.color])).toEqual([
      [0.6, "#111111"],
      [0.25, "#222222"],
      [0.15, "#333333"],
    ]);
    // A gap in the card's color keeps neighbouring slices apart.
    expect(series.itemStyle).toEqual({ borderColor: "#ffffff", borderWidth: 2 });
    expect(option.animation).toBe(true);
    expect(option.aria.decal.show).toBe(true); // a pattern besides the color
  });

  it("cycles the colors past four parts, as the table's swatches do", () => {
    const option = shareDonut(parts("0.3", "0.2", "0.2", "0.2", "0.1"), THEME, true);
    expect(option.series[0].data[4].itemStyle.color).toBe("#111111");
    expect(swatch(4)).toBe("swatch swatch-1");
    expect(swatch(1)).toBe("swatch swatch-2");
  });

  it("does not move when the system asks for reduced motion", () => {
    expect(shareDonut(parts("1"), THEME, false).animation).toBe(false);
  });
});

describe("shareBar (allocation by currency)", () => {
  it("stacks the shares in one horizontal bar from 0 to 1", () => {
    const option = shareBar(parts("0.742", "0.208", "0.05"), THEME, false);
    expect(option.animation).toBe(false);
    expect(option.xAxis).toMatchObject({ type: "value", min: 0, max: 1, show: false });
    expect(option.yAxis).toMatchObject({ type: "category", show: false });
    expect(option.series.map((s) => [s.type, s.stack, s.data, s.itemStyle.color])).toEqual([
      ["bar", "all", [0.742], "#111111"],
      ["bar", "all", [0.208], "#222222"],
      ["bar", "all", [0.05], "#333333"],
    ]);
    expect(option.series.every((s) => s.silent)).toBe(true);
  });
});

describe("monthBars (dividends and coupons by month)", () => {
  it("draws the gross of each month under its short name, with no value axis", () => {
    const months = [
      { label: "nov", value: "0" },
      { label: "dic", value: "180.00" },
      { label: "gen", value: "64.5" },
    ];
    const option = monthBars(months, THEME, true);
    expect(option.xAxis).toMatchObject({ type: "category", data: ["nov", "dic", "gen"] });
    expect(option.xAxis.axisLabel.color).toBe("#555555");
    expect(option.yAxis).toMatchObject({ type: "value", show: false });
    const [series] = option.series;
    expect([series.type, series.silent, series.data]).toEqual(["bar", true, [0, 180, 64.5]]);
    expect(series.itemStyle.color).toBe("#111111");
  });
});
