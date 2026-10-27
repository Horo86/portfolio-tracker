import type { EChartsCoreOption } from "echarts/core";
import { useEffect, useRef } from "react";

import { animate, type ChartTheme, chartTheme } from "../chartOptions";
import { init } from "./echarts";

export type ChartProps = {
  // The option for the colors of the theme in use, with or without animation.
  build: (theme: ChartTheme, animation: boolean) => object;
  theme?: string; // the user's theme, on the chart itself, so its colors are right at once
  className: string;
};

/** One ECharts chart, drawn as SVG (D-153). Hidden from screen readers and never focused: the
 * table beside it says the same in words and figures. Loaded lazily, in the echarts chunk. */
export default function Chart({ build, theme, className }: ChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<ReturnType<typeof init> | null>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const drawn = init(element, null, { renderer: "svg" });
    chart.current = drawn;
    // The card's width follows the window: the chart follows the card.
    const observer = new ResizeObserver(() => drawn.resize());
    observer.observe(element);
    return () => {
      observer.disconnect();
      drawn.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    const element = ref.current;
    if (!element || !chart.current) return;
    const colors = chartTheme(element);
    // Without its tokens (the stylesheet not loaded) a chart would be invisible: none then.
    if (colors.colors.some((color) => !color)) return;
    // Reduced motion is read at each draw: a change applies from the next one.
    chart.current.setOption(build(colors, animate()) as EChartsCoreOption, true);
  }, [build, theme]);

  return <div ref={ref} className={`chart ${className}`} data-theme={theme} aria-hidden="true" />;
}
