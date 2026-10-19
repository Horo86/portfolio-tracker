import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import i18n from "../i18n";

beforeEach(async () => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  await i18n.changeLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
