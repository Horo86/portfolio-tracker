import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useRouteError } from "react-router";

import { AuthLayout } from "./AuthLayout";
import { PageTitle } from "./PageTitle";

// Set when the page reloaded itself for a stale page file; holds the time of that reload.
const RELOADED_KEY = "pt.reloaded-for-update";
const RELOAD_WINDOW_MS = 60_000;

/**
 * A page file of an older build: after an update an open tab asks for files that are gone
 * (a missing asset answers 404, never index.html). The browsers' messages differ: Chrome
 * "Failed to fetch dynamically imported module", Firefox "error loading dynamically imported
 * module", Safari "Importing a module script failed".
 */
export function isStalePageFile(error: unknown): boolean {
  return (
    error instanceof TypeError &&
    /dynamically imported module|module script failed/i.test(error.message)
  );
}

// An object, so the tests can replace it (jsdom cannot reload).
export const page = { reload: () => window.location.reload() };

/**
 * Reloads once to fetch the new build. The flag in sessionStorage stops a loop when the
 * error is real: within a minute of a reload it does not reload again. Without storage it
 * never reloads by itself.
 */
function reloadOnce(): void {
  try {
    const last = Number(window.sessionStorage.getItem(RELOADED_KEY));
    if (last && Date.now() - last < RELOAD_WINDOW_MS) return;
    window.sessionStorage.setItem(RELOADED_KEY, String(Date.now()));
  } catch {
    return;
  }
  page.reload();
}

/**
 * Any error while showing a page. Only translated text: never the error's message or stack
 * (the browser console has them), and no inline styles (Content Security Policy).
 */
export function RouteError() {
  const { t } = useTranslation();
  const stale = isStalePageFile(useRouteError());
  useEffect(() => {
    if (stale) reloadOnce();
  }, [stale]);
  return (
    <AuthLayout>
      <PageTitle title={t(stale ? "routeError.updatedHeading" : "routeError.heading")} />
      <p className="muted">{t(stale ? "routeError.updatedText" : "routeError.text")}</p>
      <div className="choices">
        <button type="button" className="button button-primary wide" onClick={page.reload}>
          {t("routeError.reload")}
        </button>
        <a href="/" className="button wide">
          {t("notFound.home")}
        </a>
      </div>
    </AuthLayout>
  );
}
