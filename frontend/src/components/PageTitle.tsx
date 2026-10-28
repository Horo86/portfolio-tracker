import { type ReactNode, useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

let firstPage = true;

/** Gives the focus to the page's heading when what had it is gone (a dialog closed on a row
 * that was deleted): a stable place that is not a live region, so the confirmation is read
 * once, by its region. */
export function focusPageTitle() {
  document.querySelector<HTMLHeadingElement>("h1[tabindex='-1']")?.focus();
}

type Props = {
  title: string;
  children?: ReactNode;
  // A new step on the same address (e.g. the first run) takes the focus like a new page.
  step?: string;
};

/**
 * The page's <h1>. It names the browser tab and, after a navigation to another page, takes
 * the focus, so a screen reader announces the new page and the keyboard starts from its top.
 * A change of the title alone (the language) or of the query string (the account in view)
 * leaves the focus where it is. Both in the same commit as the heading (layout effects): no
 * pending effect can take the focus back after the user has moved it, and the title is never
 * a step behind the page.
 */
export function PageTitle({ title, children, step }: Props) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const ref = useRef<HTMLHeadingElement>(null);
  useLayoutEffect(() => {
    document.title = `${title} · ${t("app.title")}`;
  }, [title, t]);
  useLayoutEffect(() => {
    if (firstPage) firstPage = false;
    else ref.current?.focus();
  }, [pathname, step]);
  return (
    <h1 ref={ref} tabIndex={-1}>
      {children ?? title}
    </h1>
  );
}
