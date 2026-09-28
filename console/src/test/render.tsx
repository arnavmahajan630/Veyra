import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { I18nProvider } from "../i18n/i18n";
import { TenantScopeProvider } from "../shell/tenant";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

export function Providers({
  client,
  route = "/",
  children,
}: {
  client: QueryClient;
  route?: string;
  children: ReactNode;
}) {
  return (
    <QueryClientProvider client={client}>
      <I18nProvider>
        <MemoryRouter initialEntries={[route]}>
          <TenantScopeProvider>{children}</TenantScopeProvider>
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}

export function renderWithProviders(
  ui: ReactElement,
  options: { route?: string; client?: QueryClient } = {},
): RenderResult & { client: QueryClient } {
  const client = options.client ?? makeQueryClient();
  const result = render(ui, {
    wrapper: ({ children }) => (
      <Providers client={client} route={options.route}>
        {children}
      </Providers>
    ),
  });
  return { ...result, client };
}
