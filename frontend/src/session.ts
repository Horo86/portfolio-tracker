import { type QueryClient, useQuery } from "@tanstack/react-query";

import { api, ApiError, setSessionHandlers } from "./api/client";
import type { Me, SetupStatus } from "./api/types";
import { syncPreferences } from "./preferences";

// The logged-in user is the `me` query: a Me, or null when there is no valid session.
// The session cookie is HttpOnly: the page never sees the token, only /me tells.

export const ME_KEY = ["me"] as const;
export const SETUP_KEY = ["setup-status"] as const;

async function fetchMe(): Promise<Me | null> {
  try {
    return await api<Me>("GET", "/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: fetchMe });
}

export function useSetupStatus(enabled = true) {
  return useQuery({
    queryKey: SETUP_KEY,
    queryFn: () => api<SetupStatus>("GET", "/setup/status"),
    enabled,
  });
}

// Everything cached (queries, and mutations with their variables, e.g. passwords) except who
// is logged in, which the guard watches, and the setup status, the same for everyone.
const KEPT: ReadonlySet<unknown> = new Set([ME_KEY[0], SETUP_KEY[0]]);

function forgetUserData(client: QueryClient): void {
  client.removeQueries({ predicate: (query) => !KEPT.has(query.queryKey[0]) });
  client.getMutationCache().clear();
}

/** After login or setup: forget anything cached for a previous user, then show this one. */
export function startSession(client: QueryClient, me: Me): void {
  forgetUserData(client);
  client.setQueryData(ME_KEY, me);
  syncPreferences(me);
}

/** The session is over: nothing of the previous user stays in memory. */
export function endSession(client: QueryClient): void {
  forgetUserData(client);
  client.setQueryData(ME_KEY, null);
}

export async function logout(client: QueryClient): Promise<void> {
  try {
    await api("POST", "/auth/logout");
  } catch (error) {
    // Already gone on the server (expired, revoked): the result is the same.
    if (!(error instanceof ApiError && error.status === 401)) throw error;
  }
  endSession(client);
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
