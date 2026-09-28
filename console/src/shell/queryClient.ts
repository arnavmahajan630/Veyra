import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { queryKeys } from "../api/queries";

function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/**
 * The app's QueryClient. A 401 anywhere except /auth/me itself and the login form means
 * the session is gone: re-ask /auth/me, and its 401 turns the AuthGate into the login page.
 */
export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (isAuthError(error) && query.queryKey[0] !== queryKeys.me[0]) expire();
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _result, mutation) => {
        if (isAuthError(error) && mutation.options.mutationKey?.[0] !== "login") expire();
      },
    }),
    defaultOptions: {
      queries: {
        retry: (failures, error) => !(error instanceof ApiError && error.status < 500) && failures < 2,
        refetchOnWindowFocus: false,
      },
    },
  });

  function expire(): void {
    void client.invalidateQueries({ queryKey: queryKeys.me });
  }

  return client;
}
