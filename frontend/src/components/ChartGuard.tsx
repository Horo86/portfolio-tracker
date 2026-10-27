import { Component, type ReactNode } from "react";

/** A chart only repeats the table beside it: if it cannot load (a chunk gone after an upgrade,
 * a network error) or draw, it shows nothing, and the page keeps its figures. */
export class ChartGuard extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}
