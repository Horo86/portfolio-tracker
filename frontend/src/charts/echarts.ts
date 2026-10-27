// ECharts for the dashboard (D-153, D-138): only the parts the charts use, drawn as SVG. This
// module and the library land in their own lazy chunk ("echarts…", vite.config.ts), loaded
// after the figures and tables, never with the first screen.
import { BarChart, PieChart } from "echarts/charts";
import { AriaComponent, GridComponent } from "echarts/components";
import { init, use as register } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";

register([PieChart, BarChart, GridComponent, AriaComponent, SVGRenderer]);

export { init };
