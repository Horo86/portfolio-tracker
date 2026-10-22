import "@fontsource/manrope/latin-400.css";
import "@fontsource/manrope/latin-500.css";
import "@fontsource/manrope/latin-600.css";
import "@fontsource/manrope/latin-700.css";
import "@fontsource/manrope/latin-800.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { i18nReady } from "./i18n";
import "./styles.css";
import { applyTheme, storedTheme } from "./theme";

// Before the first render, so the page never flashes in another theme (no inline script:
// the build stays compatible with a strict Content Security Policy).
applyTheme(storedTheme());

// After the active language's texts: the first screen never shows untranslated keys. If they
// cannot be loaded, the app still starts rather than staying blank.
const render = () =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
void i18nReady.then(render, render);
