import { lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";
import { Outlet } from "react-router";

import { useAccountInView } from "../accounts";
import type { Me } from "../api/types";
import { useCompact } from "../useCompact";
import { BottomNav } from "./BottomNav";
import { Sidebar } from "./Sidebar";
import { Loading } from "./Status";
import { TopStrip } from "./TopStrip";

// Shown only after an administrator turned the user's 2FA off: a chunk of its own (D-138).
const TwoFactorNotice = lazy(() => import("./TwoFactorNotice"));

/**
 * The shell of every page with a session (mockups 13, 14): the sidebar and the content from
 * 1024 px; below, a top strip, the content and a bar at the bottom (D-189). The content stays
 * mounted in the same place when the window crosses the breakpoint.
 */
export function AppLayout({ me }: { me: Me }) {
  const { t } = useTranslation();
  const compact = useCompact();
  // Every link keeps the account in view (Q-6).
  const { search } = useAccountInView();

  return (
    <div className={compact ? "shell shell-compact" : "shell"}>
      <a className="skip-link" href="#main">
        {t("app.skipToContent")}
      </a>
      {compact ? <TopStrip me={me} /> : <Sidebar me={me} search={search} />}
      <main id="main" className="content" tabIndex={-1}>
        {me.two_factor_disabled_at && (
          <Suspense fallback={null}>
            <TwoFactorNotice disabledAt={me.two_factor_disabled_at} />
          </Suspense>
        )}
        <Suspense fallback={<Loading />}>
          <Outlet />
        </Suspense>
      </main>
      {compact && <BottomNav me={me} search={search} />}
    </div>
  );
}
