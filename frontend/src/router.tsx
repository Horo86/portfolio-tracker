import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect } from "react";
import { Navigate, Outlet, type RouteObject, useLocation } from "react-router";

import { AppLayout } from "./components/AppLayout";
import { RouteError } from "./components/RouteError";
import { LoadError, Loading } from "./components/Status";
import { syncPreferences } from "./preferences";
import { loggedOut, ME_KEY, useMe, useSetupStatus } from "./session";

// Each page is its own chunk (ADR-0003: route-level code splitting).
const Login = lazy(() => import("./pages/Login"));
const FirstRun = lazy(() => import("./pages/FirstRun"));
const ForcedPasswordChange = lazy(() => import("./pages/ForcedPasswordChange"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Accounts = lazy(() => import("./pages/Accounts"));
const Transactions = lazy(() => import("./pages/Transactions"));
const NewTransaction = lazy(() => import("./pages/TransactionForm"));
const EditTransaction = lazy(() => import("./pages/EditTransaction"));
const Settings = lazy(() => import("./pages/Settings"));
const About = lazy(() => import("./pages/About"));
const NotFound = lazy(() => import("./pages/NotFound"));

/** Keeps language and theme in step with the user's settings once logged in (Q-7). */
function Root() {
  const me = useMe();
  const language = me.data?.language;
  const theme = me.data?.theme;
  useEffect(() => {
    if (language && theme) syncPreferences({ language, theme });
  }, [language, theme]);
  return (
    <Suspense fallback={<Loading />}>
      <Outlet />
    </Suspense>
  );
}

/**
 * Every page except login, first run and the forced password change needs a session.
 * A fresh instance (no users) goes to the first run; a temporary password must be changed.
 */
export function RequireSession() {
  const location = useLocation();
  const client = useQueryClient();
  const me = useMe();
  const setup = useSetupStatus(me.data === null);

  if (me.isPending) return <Loading />;
  if (me.isError) {
    return (
      <main className="auth-page">
        <LoadError
          error={me.error}
          onRetry={() => void client.invalidateQueries({ queryKey: ME_KEY })}
        />
      </main>
    );
  }
  if (me.data === null) {
    if (setup.isPending) return <Loading />;
    if (setup.data?.setup_required) return <Navigate to="/welcome" replace />;
    // After an expired session the login returns to this page; after Log out it does not.
    return <Navigate to="/login" replace state={loggedOut(client) ? null : { from: location }} />;
  }
  if (me.data.must_change_password) return <Navigate to="/password-change" replace />;
  return <AppLayout me={me.data} />;
}

export const routes: RouteObject[] = [
  {
    element: <Root />,
    // Our own error screen: React Router's default one shows the message and the stack.
    errorElement: <RouteError />,
    children: [
      { path: "/login", element: <Login /> },
      { path: "/welcome", element: <FirstRun /> },
      { path: "/password-change", element: <ForcedPasswordChange /> },
      {
        element: <RequireSession />,
        children: [
          { index: true, element: <Dashboard /> },
          { path: "/accounts", element: <Accounts /> },
          { path: "/transactions", element: <Transactions /> },
          { path: "/transactions/new", element: <NewTransaction /> },
          { path: "/transactions/:id/edit", element: <EditTransaction /> },
          { path: "/settings", element: <Settings /> },
          { path: "/about", element: <About /> },
          { path: "*", element: <NotFound /> },
        ],
      },
    ],
  },
];
