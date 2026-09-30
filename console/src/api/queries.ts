import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, CONTROL, DEMO, EVIDENCE, LINEAGE } from "./client";
import type {
  ApiKeyRow,
  AuditRow,
  ContractDetail,
  ContractSummary,
  DemoScenario,
  DemoStageStatus,
  DiffOut,
  Draft,
  DraftEdit,
  DriftItem,
  EventDetail,
  KeyCard,
  LedgerRootsResponse,
  Me,
  Overview,
  PreflightCheck,
  ReplayJob,
  ResetStarted,
  ResetStatus,
  TamperActive,
  RouteSpec,
  SearchResult,
  Source,
  SourceHealth,
  TamperResult,
  Tenant,
  VerifyReport,
  VersionDetail,
} from "./types";

export const queryKeys = {
  me: ["me"] as const,
  tenants: ["tenants"] as const,
  sources: (tenant: string | null) => ["sources", tenant] as const,
  overview: (tenant: string | null) => ["overview", tenant] as const,
  health: (tenant: string | null) => ["health", tenant] as const,
  keys: (sourceId: string) => ["keys", sourceId] as const,
  contracts: (tenant: string | null) => ["contracts", tenant] as const,
  contract: (id: string) => ["contracts", "detail", id] as const,
  version: (id: string, v: number) => ["contracts", "detail", id, v] as const,
  diff: (id: string, from: number, to: number) => ["contracts", "diff", id, from, to] as const,
  drift: (state: string | null, sourceId: string | null = null) => ["drift", state, sourceId] as const,
  driftItem: (id: string) => ["driftItem", id] as const,
  draft: (id: string) => ["draft", id] as const,
  replays: (contractId: string) => ["replays", contractId] as const,
  replay: (jobId: string) => ["replay", jobId] as const,
  audit: ["audit"] as const,
  routes: ["routes"] as const,
  search: (q: string, tenant: string | null) => ["lineage", "search", q, tenant] as const,
  eventDetail: (uid: string) => ["lineage", "event", uid] as const,
  verify: (uid: string) => ["evidence", "verify", uid] as const,
  roots: (limit: number) => ["evidence", "roots", limit] as const,
  pubkey: ["evidence", "pubkey"] as const,
  demoScenario: ["demo", "scenario"] as const,
  demoStage: (stage: number) => ["demo", "stage", stage] as const,
  preflight: ["demo", "preflight"] as const,
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

export function useContracts(tenant: string | null) {
  return useQuery({
    queryKey: queryKeys.contracts(tenant),
    queryFn: () => api.get<ContractSummary[]>(`${CONTROL}/contracts${tenantQuery(tenant)}`),
  });
}

export function useContract(id: string) {
  return useQuery({
    queryKey: queryKeys.contract(id),
    queryFn: () => api.get<ContractDetail>(`${CONTROL}/contracts/${encodeURIComponent(id)}`),
    enabled: id !== "",
  });
}

export function useContractVersion(id: string, version: number | null) {
  return useQuery({
    queryKey: queryKeys.version(id, version ?? 0),
    queryFn: () => api.get<VersionDetail>(`${CONTROL}/contracts/${encodeURIComponent(id)}/versions/${version}`),
    enabled: version !== null,
  });
}

export function useDiff(id: string, from: number, to: number, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.diff(id, from, to),
    queryFn: () => api.get<DiffOut>(`${CONTROL}/contracts/${encodeURIComponent(id)}/diff?from=${from}&to=${to}`),
    enabled,
  });
}

export function useLifecycle(contractId: string) {
  const client = useQueryClient();
  const done = () => client.invalidateQueries({ queryKey: ["contracts"] });
  const base = `${CONTROL}/contracts/${encodeURIComponent(contractId)}`;
  return {
    approve: useMutation({ mutationFn: (v: number) => api.post<VersionDetail>(`${base}/versions/${v}/approve`), onSuccess: done }),
    promote: useMutation({ mutationFn: (v: number) => api.post<VersionDetail>(`${base}/versions/${v}/promote`), onSuccess: done }),
    rollback: useMutation({
      mutationFn: (to: number) => api.post<VersionDetail>(`${base}/rollback`, { to_version: to }),
      onSuccess: done,
    }),
  };
}

export function useDrift(state: string | null, sourceId: string | null = null) {
  const params = new URLSearchParams();
  if (state) params.set("state", state);
  if (sourceId) params.set("source_id", sourceId); // control-api's GET /drift filter
  const query = params.toString();
  return useQuery({
    queryKey: queryKeys.drift(state, sourceId),
    queryFn: () => api.get<DriftItem[]>(`${CONTROL}/drift${query ? `?${query}` : ""}`),
  });
}

export function useDriftItem(id: string) {
  return useQuery({
    queryKey: queryKeys.driftItem(id),
    queryFn: () => api.get<DriftItem>(`${CONTROL}/drift/${encodeURIComponent(id)}`),
  });
}

export function useDismissDrift() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<DriftItem>(`${CONTROL}/drift/${encodeURIComponent(id)}/dismiss`),
    onSuccess: (item) => {
      client.setQueryData(queryKeys.driftItem(item.drift_id), item);
      return client.invalidateQueries({ queryKey: ["drift"] });
    },
  });
}

export function useStartDraft(driftId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (mode?: "cache") =>
      api.post<{ draft_id: string }>(`${CONTROL}/drift/${encodeURIComponent(driftId)}/draft`, mode ? { mode } : {}),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.driftItem(driftId) }),
  });
}

/** A draft; polls while it is still drafting, so a missed SSE `draft` event can't strand the page. */
export function useDraft(id: string | null) {
  return useQuery({
    queryKey: queryKeys.draft(id ?? ""),
    queryFn: () => api.get<Draft>(`${CONTROL}/drafts/${encodeURIComponent(id ?? "")}`),
    enabled: id !== null,
    refetchInterval: (query) => (query.state.data?.state === "drafting" ? DRAFT_POLL_MS : false),
  });
}

export const DRAFT_POLL_MS = 1_000;

export function usePatchDraft(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (edit: DraftEdit) => api.patch<Draft>(`${CONTROL}/drafts/${encodeURIComponent(id)}`, edit),
    onSuccess: (draft) => client.setQueryData(queryKeys.draft(id), draft),
  });
}

export function useSubmitDraft(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<VersionDetail>(`${CONTROL}/drafts/${encodeURIComponent(id)}/submit`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.draft(id) });
      void client.invalidateQueries({ queryKey: ["contracts"] });
    },
  });
}

export function useStartReplay() {
  return useMutation({
    mutationFn: (body: { contract_id: string; template_sigs: string[] }) =>
      api.post<ReplayJob>(`${CONTROL}/replay`, body),
  });
}

/** A replay job; polls while unfinished so a missed SSE `done` can't strand the bar. */
export function useReplay(jobId: string | null) {
  return useQuery({
    queryKey: queryKeys.replay(jobId ?? ""),
    queryFn: () => api.get<ReplayJob>(`${CONTROL}/replay/${encodeURIComponent(jobId ?? "")}`),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state === "done" || state === "timed_out" || state === "failed" ? false : REPLAY_POLL_MS;
    },
  });
}

export const REPLAY_POLL_MS = 1_500;

export function useAudit() {
  return useQuery({ queryKey: queryKeys.audit, queryFn: () => api.get<AuditRow[]>(`${CONTROL}/audit`) });
}

export function useRoutes() {
  return useQuery({ queryKey: queryKeys.routes, queryFn: () => api.get<{ routes: RouteSpec[] }>(`${CONTROL}/routes`) });
}

export function useCreateSource() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Source> & { id: string; tenant_id: string }) => api.post<Source>(`${CONTROL}/sources`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: ["sources"] }),
  });
}

export function useUseLibrary() {
  return useMutation({
    mutationFn: (body: { source_id: string; pack: string }) =>
      api.post<VersionDetail>(`${CONTROL}/onboarding/use-library`, body),
  });
}

// ---------------------------------------------------------------- B6 Hooks
export function useLineageSearch(q: string, tenant: string | null = null) {
  const trimmed = q.trim();
  const query = new URLSearchParams();
  if (trimmed) query.set("q", trimmed);
  if (tenant && tenant !== "*") query.set("tenant", tenant);
  const qs = query.toString();
  return useQuery({
    queryKey: queryKeys.search(trimmed, tenant),
    queryFn: () => api.get<SearchResult>(`${LINEAGE}/search${qs ? `?${qs}` : ""}`),
    enabled: trimmed.length > 0,
  });
}

export function useEventDetail(uid: string | null) {
  return useQuery({
    queryKey: queryKeys.eventDetail(uid ?? ""),
    queryFn: () => api.get<EventDetail>(`${LINEAGE}/events/${encodeURIComponent(uid ?? "")}`),
    enabled: uid !== null && uid.length > 0,
  });
}

export function useVerify(uid: string | null, enabled: boolean = false) {
  return useQuery({
    queryKey: queryKeys.verify(uid ?? ""),
    queryFn: () => api.get<VerifyReport>(`${EVIDENCE}/${encodeURIComponent(uid ?? "")}/verify`),
    enabled: enabled && uid !== null && uid.length > 0,
  });
}

export function useVerifyMutation() {
  return useMutation({
    mutationFn: (uid: string) => api.get<VerifyReport>(`${EVIDENCE}/${encodeURIComponent(uid)}/verify`),
  });
}

export function useEvidenceRoots(limit: number = 50) {
  return useQuery({
    queryKey: queryKeys.roots(limit),
    queryFn: () => api.get<LedgerRootsResponse>(`${EVIDENCE}/roots?limit=${limit}`),
  });
}

export function useEvidencePubkey() {
  return useQuery({
    queryKey: queryKeys.pubkey,
    // /pubkey answers text/plain PEM, not JSON.
    queryFn: () => api.text(`${EVIDENCE}/pubkey`),
  });
}

export function useEvidenceExport() {
  return useMutation({
    mutationFn: (uid: string) => api.blob(`${EVIDENCE}/export/${encodeURIComponent(uid)}`),
  });
}

// ---------------------------------------------------------------- B7 Demo Hooks
export function useDemoScenario() {
  return useQuery({
    queryKey: queryKeys.demoScenario,
    queryFn: () => api.get<DemoScenario>(`${DEMO}/scenario`),
  });
}

/** Live stage state, including each `expect` result. Polls while a stage is running. */
export function useDemoStage(enabled: boolean = true, refetchMs: number = 2000) {
  return useQuery({
    queryKey: queryKeys.demoStage(0),
    queryFn: () => api.get<DemoStageStatus>(`${DEMO}/stage/status`),
    enabled,
    refetchInterval: refetchMs,
  });
}

export function useTriggerStage() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (stage: number) => api.post<{ ok: boolean; stage: number }>(`${DEMO}/stage/${stage}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["demo"] });
    },
  });
}

export function usePreflight(enabled: boolean = true) {
  return useQuery({
    queryKey: queryKeys.preflight,
    queryFn: () => api.get<PreflightCheck[]>(`${DEMO}/preflight`),
    enabled,
  });
}

/** Starts a reset; it runs in the background and is followed with `useResetStatus`. */
export function useDemoReset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ResetStarted>(`${DEMO}/reset`),
    onSuccess: () => {
      client.invalidateQueries();
    },
  });
}

/** Polls the reset's step-by-step progress while one is running. */
export function useResetStatus(enabled: boolean) {
  return useQuery({
    queryKey: ["demo", "reset-status"],
    queryFn: () => api.get<ResetStatus>(`${DEMO}/reset/status`),
    enabled,
    refetchInterval: enabled ? 1000 : false,
  });
}

/** What is tampered right now, and which modes the lab offers. */
export function useTamperActive() {
  return useQuery({
    queryKey: ["demo", "tamper-active"],
    queryFn: () => api.get<TamperActive>(`${DEMO}/tamper/active`),
  });
}

/** Tells the engine which hotkeys this console registered, so preflight can check. */
export function useReportHotkeys() {
  return useMutation({
    mutationFn: (combos: string[]) => api.post<{ ok: boolean }>(`${DEMO}/hotkeys`, { combos }),
  });
}

export function useDemoTamper() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { mode: string; event_uid?: string }) => api.post<TamperResult>(`${DEMO}/tamper`, body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["evidence"] });
    },
  });
}

export function useDemoUntamper() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: { event_uid?: string }) =>
      api.post<{ ok: boolean }>(`${DEMO}/untamper`, body ?? {}),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["evidence"] });
    },
  });
}

