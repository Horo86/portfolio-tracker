import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";

import i18n from "../i18n";
import { takeUnexpected } from "./fetchStub";

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

beforeEach(async () => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  await i18n.changeLanguage("en");
});

afterEach(() => {
  cleanup();
  expect(takeUnexpected(), "requests to routes the test did not stub").toEqual([]);
  vi.unstubAllGlobals();
});
