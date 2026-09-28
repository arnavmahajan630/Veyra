import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { useMe } from "../api/queries";
import { PLATFORM } from "../api/types";

interface TenantScope {
  /** The tenant every page filters by; null = all tenants (platform users only). */
  scope: string | null;
  canChoose: boolean;
  choose: (tenant: string | null) => void;
}

const TenantScopeContext = createContext<TenantScope | null>(null);

export function TenantScopeProvider({ children }: { children: ReactNode }) {
  const me = useMe().data;
  const [chosen, setChosen] = useState<string | null>(null);
  const platform = me?.tenant === PLATFORM;
  const ownTenant = me && !platform ? me.tenant : null;

  const value = useMemo<TenantScope>(
    () => ({ scope: platform ? chosen : ownTenant, canChoose: platform, choose: setChosen }),
    [platform, chosen, ownTenant],
  );
  return <TenantScopeContext.Provider value={value}>{children}</TenantScopeContext.Provider>;
}

export function useTenantScope(): TenantScope {
  const value = useContext(TenantScopeContext);
  if (!value) throw new Error("useTenantScope() needs a <TenantScopeProvider> above it");
  return value;
}
