import { type ReactNode, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

let firstPage = true;

/**
 * The page's <h1>. It names the browser tab and, after a navigation, takes the focus, so a
 * screen reader announces the new page and the keyboard starts from its top.
 */
export function PageTitle({ title, children }: { title: string; children?: ReactNode }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    document.title = `${title} · ${t("app.title")}`;
  }, [title, t]);
  useEffect(() => {
    if (firstPage) firstPage = false;
    else ref.current?.focus();
  }, [title]);
  return (
    <h1 ref={ref} tabIndex={-1}>
      {children ?? title}
    </h1>
  );
}
