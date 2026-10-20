import { type QueryClient, useQuery } from "@tanstack/react-query";

import { api, ApiError, nextSession, setSessionHandlers } from "./api/client";
import type { Health, Me, SetupStatus } from "./api/types";
import { syncPreferences } from "./preferences";

// The logged-in user is the `me` query: a Me, or null when there is no valid session.
// The session cookie is HttpOnly: the page never sees the token, only /me tells.

export const ME_KEY = ["me"] as const;
export const SETUP_KEY = ["setup-status"] as const;
const HEALTH_KEY = ["health"] as const;
// How the last session ended: "logout" (the Log out button) or "expired" (a 401). After a
// logout the login page has no page to return to: the next person may be someone else.
const ENDED_KEY = ["session-ended"] as const;

type Ended = "logout" | "expired";

async function fetchMe(signal: AbortSignal): Promise<Me | null> {
  try {
    return await api<Me>("GET", "/me", undefined, signal);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: ({ signal }) => fetchMe(signal) });
}

export function useSetupStatus(enabled = true) {
  return useQuery({
    queryKey: SETUP_KEY,
    queryFn: ({ signal }) => api<SetupStatus>("GET", "/setup/status", undefined, signal),
    enabled,
  });
}

// The same for everyone: the setup status and the version.
const SHARED: ReadonlySet<unknown> = new Set([SETUP_KEY[0], HEALTH_KEY[0]]);
const KEPT: ReadonlySet<unknown> = new Set([...SHARED, ME_KEY[0], ENDED_KEY[0]]);

/**
 * Forgets everything of the previous session: requests still running are cancelled (a late
 * answer must not land in the next session, e.g. a /me overwriting the user just set), then
 * the cached queries and the mutations with their variables are removed. Only who is logged
 * in (watched by the guard) and what is the same for everyone stay.
 */
function forgetUserData(client: QueryClient): void {
  void client.cancelQueries({ predicate: (query) => !SHARED.has(query.queryKey[0]) });
  client.removeQueries({ predicate: (query) => !KEPT.has(query.queryKey[0]) });
  client.getMutationCache().clear();
  nextSession();
}

/** After login or setup: forget anything cached for a previous user, then show this one. */
export function startSession(client: QueryClient, me: Me): void {
  forgetUserData(client);
  client.setQueryData(ENDED_KEY, null);
  client.setQueryData(ME_KEY, me);
  syncPreferences(me);
}

/** The session is over: nothing of the previous user stays in memory. */
export function endSession(client: QueryClient, how: Ended = "expired"): void {
  forgetUserData(client);
  client.setQueryData(ENDED_KEY, how);
  client.setQueryData(ME_KEY, null);
}

/** Whether the last session ended with the Log out button. */
export function loggedOut(client: QueryClient): boolean {
  return client.getQueryData<Ended | null>(ENDED_KEY) === "logout";
}

export async function logout(client: QueryClient): Promise<void> {
  try {
    await api("POST", "/auth/logout");
  } catch (error) {
    // Already gone on the server (expired, revoked): the result is the same.
    if (!(error instanceof ApiError && error.status === 401)) throw error;
  }
  endSession(client, "logout");
}

/** A 401 on any call ends the session in the page; the guard then shows the login. */
export function installSessionHandlers(client: QueryClient): void {
  setSessionHandlers({
    onUnauthorized: () => {
      if (client.getQueryData(ME_KEY)) endSession(client);
    },
    onPasswordChangeRequired: () => void client.invalidateQueries({ queryKey: ME_KEY }),
  });
}

export function useHealth() {
  return useQuery({
    queryKey: HEALTH_KEY,
    queryFn: ({ signal }) => api<Health>("GET", "/api/health", undefined, signal),
    staleTime: Infinity,
  });
}
