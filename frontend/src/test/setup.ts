import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeAll, beforeEach, expect, vi } from "vitest";

import i18n, { i18nReady, LANGUAGES, loadLanguage } from "../i18n";
import { takeUnexpected } from "./fetchStub";

// How long findBy… and waitFor wait for what they look for: they return as soon as it is there.
// The default 1 s was too short on a busy machine, where a test's first render must also load
// its lazy page module cold: the flaky failures were such waits, not wrong results.
configure({ asyncUtilTimeout: 5000 });

// jsdom has no modal dialogs: here opening and closing only set the `open` attribute. The
// browser's own behaviour (focus kept inside, Escape, inert page) is checked end to end.
if (typeof HTMLDialogElement.prototype.showModal !== "function") {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
    this.querySelector<HTMLElement>("input, select, button")?.focus();
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
}

// The dashboard's charts (ECharts, SVG) are not drawn in jsdom: a stand-in shows the option it
// would draw, so a test checks what would be drawn. Their own look is checked end to end.
vi.mock("../charts/Chart", () => ({
  default: ({ build }: { build: (theme: object, animation: boolean) => object }) =>
    createElement("div", {
      "data-testid": "chart",
      "aria-hidden": "true",
      "data-option": JSON.stringify(
        build({ colors: ["#000001", "#000002", "#000003", "#000004"], surface: "#fff", muted: "#555" }, true),
      ),
    }),
}));

// Both languages, so a test can use either one at once.
beforeAll(async () => {
  await i18nReady;
  await Promise.all(LANGUAGES.map(loadLanguage));
});

beforeEach(async () => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  await i18n.changeLanguage("en");
});

afterEach(() => {
  cleanup();
  expect(takeUnexpected(), "requests to routes the test did not stub").toEqual([]);
  vi.unstubAllGlobals();
});
