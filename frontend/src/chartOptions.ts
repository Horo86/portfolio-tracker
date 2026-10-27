// The options of the dashboard's charts (D-153), built without ECharts so they can be tested
// alone. A chart only draws what the table beside it says in words and figures: the shares
// and amounts become numbers here for the geometry only, never to be shown or added up.

export type ChartTheme = { colors: string[]; surface: string; muted: string };

const SLOTS = 4; // --chart-1..4, cycled past four parts

const colorAt = (theme: ChartTheme, i: number) => theme.colors[i % theme.colors.length];

/** The class of the color swatch of the i-th part in a table: the chart's legend. */
export const swatch = (i: number) => `swatch swatch-${(i % SLOTS) + 1}`;

/** The theme's chart colors, read from its tokens (themes.css). */
export function chartTheme(element: Element = document.documentElement): ChartTheme {
  const style = getComputedStyle(element);
  const token = (name: string) => style.getPropertyValue(`--${name}`).trim();
  return {
    colors: Array.from({ length: SLOTS }, (_, i) => token(`chart-${i + 1}`)),
    surface: token("surface"),
    muted: token("muted"),
  };
}

/** Whether the system asks for less motion: the charts then appear without animation. */
export function animate(): boolean {
  return !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

// Nothing in a chart reacts to the pointer or takes the focus, and a pattern joins the color.
const COMMON = { aria: { enabled: false, decal: { show: true } } };

type Part = { share: string };

/** Allocation by type: a donut, one slice per row of its table, in the same order. */
export function shareDonut(parts: Part[], theme: ChartTheme, animation: boolean) {
  return {
    ...COMMON,
    animation,
    series: [
      {
        type: "pie" as const,
        radius: ["55%", "85%"],
        silent: true,
        label: { show: false },
        itemStyle: { borderColor: theme.surface, borderWidth: 2 },
        data: parts.map((part, i) => ({
          value: Number(part.share),
          itemStyle: { color: colorAt(theme, i) },
        })),
      },
    ],
  };
}

/** Allocation by currency: one bar from 0 to 1, a stretch per row of its table. */
export function shareBar(parts: Part[], theme: ChartTheme, animation: boolean) {
  return {
    ...COMMON,
    animation,
    grid: { left: 0, right: 0, top: 0, bottom: 0 },
    xAxis: { type: "value" as const, min: 0, max: 1, show: false },
    yAxis: { type: "category" as const, show: false, data: [""] },
    series: parts.map((part, i) => ({
      type: "bar" as const,
      stack: "all",
      silent: true,
      barWidth: "100%",
      data: [Number(part.share)],
      itemStyle: { color: colorAt(theme, i), borderColor: theme.surface, borderWidth: 1 },
    })),
  };
}

/** Dividends and coupons: the gross of each month as a bar, under the month's short name; the
 * amounts are in the table below, so there is no value axis. */
export function monthBars(
  months: { label: string; value: string }[],
  theme: ChartTheme,
  animation: boolean,
) {
  return {
    ...COMMON,
    animation,
    grid: { left: 0, right: 0, top: 8, bottom: 24 },
    xAxis: {
      type: "category" as const,
      data: months.map((m) => m.label),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: theme.muted } },
      axisLabel: { color: theme.muted, interval: 0 },
    },
    yAxis: { type: "value" as const, show: false },
    series: [
      {
        type: "bar" as const,
        silent: true,
        barMaxWidth: 24,
        data: months.map((m) => Number(m.value)),
        itemStyle: { color: colorAt(theme, 0), borderRadius: [4, 4, 0, 0] },
      },
    ],
  };
}
