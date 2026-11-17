import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChartGuard } from "../components/ChartGuard";
import { applyTheme, useShownTheme, watchSystem } from "../theme";

// The real wrapper (the other tests draw a stand-in, test/setup.ts), over a fake ECharts.
vi.unmock("./Chart");
const fake = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
const init = vi.fn<(...args: unknown[]) => typeof fake>(() => fake);
vi.mock("./echarts", () => ({ init: (...args: unknown[]) => init(...args) }));

const { default: Chart } = await import("./Chart");

const TOKENS = ["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--surface", "--muted"];

describe("Chart (the ECharts wrapper)", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    for (const name of TOKENS) document.documentElement.style.setProperty(name, "#123456");
    init.mockClear();
    fake.setOption.mockClear();
    fake.dispose.mockClear();
  });
  afterEach(() => {
    for (const name of TOKENS) document.documentElement.style.removeProperty(name);
  });

  it("draws as SVG, hidden from readers, in the theme's colors; redraws on a new theme", () => {
    const build = vi.fn((theme: { colors: string[] }, animation: boolean) => ({ theme, animation }));
    const { container, rerender, unmount } = render(
      <Chart build={build} theme="sobrio" className="chart-donut" />,
    );
    const element = container.querySelector(".chart") as HTMLElement;
    expect(element).toHaveAttribute("aria-hidden", "true");
    expect(element).toHaveAttribute("data-theme", "sobrio");
    expect(init).toHaveBeenCalledWith(element, null, { renderer: "svg" });
    expect(fake.setOption).toHaveBeenCalledTimes(1);
    const [option, replace] = fake.setOption.mock.calls[0];
    expect(option.theme.colors).toEqual(["#123456", "#123456", "#123456", "#123456"]);
    expect(option.animation).toBe(true); // no reduced-motion request in jsdom
    expect(replace).toBe(true);

    rerender(<Chart build={build} theme="ambra" className="chart-donut" />);
    expect(fake.setOption).toHaveBeenCalledTimes(2);
    unmount();
    expect(fake.dispose).toHaveBeenCalledTimes(1);
  });

  it("redraws when the theme on screen changes: the device switches to dark (D-176)", () => {
    let dark = false;
    const listeners = new Set<() => void>();
    vi.stubGlobal("matchMedia", () => ({
      get matches() {
        return dark;
      },
      addEventListener: (_: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
    }));
    applyTheme("sobrio");
    const stop = watchSystem();
    function Shown() {
      return <Chart build={() => ({})} theme={useShownTheme()} className="chart-donut" />;
    }
    const { container } = render(<Shown />);
    const element = container.querySelector(".chart") as HTMLElement;
    expect(element).toHaveAttribute("data-theme", "sobrio");
    expect(fake.setOption).toHaveBeenCalledTimes(1);

    act(() => {
      dark = true;
      for (const listener of listeners) listener();
    });
    expect(element).toHaveAttribute("data-theme", "scuro");
    expect(fake.setOption).toHaveBeenCalledTimes(2);
    stop();
  });

  it("draws nothing without the theme's colors, rather than an invisible chart", () => {
    document.documentElement.style.removeProperty("--chart-3");
    render(<Chart build={() => ({})} className="chart-donut" />);
    expect(init).toHaveBeenCalledTimes(1);
    expect(fake.setOption).not.toHaveBeenCalled();
  });
});

describe("ChartGuard", () => {
  it("a chart that fails shows nothing and leaves the page alone", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    function Broken(): never {
      throw new Error("chunk not found");
    }
    const { container } = render(
      <div>
        <p>figures</p>
        <ChartGuard>
          <Broken />
        </ChartGuard>
      </div>,
    );
    act(() => {});
    expect(container).toHaveTextContent("figures");
    expect(container.querySelector("p")).toBeInTheDocument();
    quiet.mockRestore();
  });
});
