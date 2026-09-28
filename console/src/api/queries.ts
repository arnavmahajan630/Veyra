import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, CONTROL, LINEAGE } from "./client";
import type { ApiKeyRow, KeyCard, Me, Overview, Source, SourceHealth, Tenant } from "./types";

export const queryKeys = {
  me: ["me"] as const,
  tenants: ["tenants"] as const,
  sources: (tenant: string | null) => ["sources", tenant] as const,
  overview: (tenant: string | null) => ["overview", tenant] as const,
  health: (tenant: string | null) => ["health", tenant] as const,
  keys: (sourceId: string) => ["keys", sourceId] as const,
};

function tenantQuery(tenant: string | null): string {
  return tenant ? `?tenant=${encodeURIComponent(tenant)}` : "";
}

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: () => api.get<Me>(`${CONTROL}/auth/me`),
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    // The key lets the app's 401 handler (Task 12) tell a wrong password from an expired session.
    mutationKey: ["login"],
    mutationFn: (body: { email: string; password: string }) =>
      api.post<Me>(`${CONTROL}/auth/login`, body),
    onSuccess: (me) => {
      client.setQueryData(queryKeys.me, me);
    },
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>(`${CONTROL}/auth/logout`),
    onSettled: () => client.resetQueries(),
  });
}

export function useDemoSwitch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (email: string) => api.post<Me>(`${CONTROL}/auth/demo-switch`, { email }),
    onSuccess: async (me) => {
      client.setQueryData(queryKeys.me, me);
      await client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "me" });
    },
  });
}

export function useTenants(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.tenants,
    queryFn: () => api.get<Tenant[]>(`${CONTROL}/tenants`),
    enabled,
  });
}

export function useSources(tenant: string | null) {
  return useQuery({
    queryKey: queryKeys.sources(tenant),
    queryFn: () => api.get<Source[]>(`${CONTROL}/sources${tenantQuery(tenant)}`),
  });
}

export function useOverview(tenant: string | null) {
  return useQuery({
    queryKey: queryKeys.overview(tenant),
    queryFn: () => api.get<Overview>(`${LINEAGE}/overview${tenantQuery(tenant)}`),
  });
}

export function useSourceHealth(tenant: string | null) {
  return useQuery({
    queryKey: queryKeys.health(tenant),
    queryFn: () => api.get<SourceHealth[]>(`${LINEAGE}/sources${tenantQuery(tenant)}`),
  });
}

export function useSourceKeys(sourceId: string) {
  return useQuery({
    queryKey: queryKeys.keys(sourceId),
    queryFn: () => api.get<ApiKeyRow[]>(`${CONTROL}/sources/${encodeURIComponent(sourceId)}/keys`),
  });
}

export function useIssueKey(sourceId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<KeyCard>(`${CONTROL}/sources/${encodeURIComponent(sourceId)}/keys`, {}),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.keys(sourceId) }),
  });
}

export function useRevokeKey(sourceId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (keyId: string) =>
      api.post<{ key_id: string; status: string }>(`${CONTROL}/keys/${encodeURIComponent(keyId)}/revoke`),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.keys(sourceId) }),
  });
}
