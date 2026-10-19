import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";

import { ApiError } from "./api/client";
import { routes } from "./router";
import { installSessionHandlers } from "./session";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // A client error (4xx) does not get better by asking again.
        retry: (failures, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) &&
          failures < 2,
      },
      mutations: { retry: false },
    },
  });
}

export default function App() {
  const [client] = useState(createQueryClient);
  const [router] = useState(() => createBrowserRouter(routes));
  useEffect(() => installSessionHandlers(client), [client]);
  return (
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
