import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";

import i18n from "../i18n";
import { takeUnexpected } from "./fetchStub";

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
