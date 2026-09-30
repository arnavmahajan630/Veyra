import { Navigate, Route, Routes } from "react-router";
import type { Me } from "../api/types";
import AuditPage from "../pages/audit/AuditPage";
import ContractDetailPage from "../pages/contracts/ContractDetailPage";
import ContractsPage from "../pages/contracts/ContractsPage";
import DeliveryPage from "../pages/delivery/DeliveryPage";
import DemoPage from "../pages/demo/DemoPage";
import ComponentsPage from "../pages/dev/ComponentsPage";
import DriftDetailPage from "../pages/drift/DriftDetailPage";
import DriftInboxPage from "../pages/drift/DriftInboxPage";
import EvidencePage from "../pages/evidence/EvidencePage";
import { EventDetailView } from "../pages/lineage/EventDetailView";
import LineagePage from "../pages/lineage/LineagePage";
import OnboardPage from "../pages/onboard/OnboardPage";
import OverviewPage from "../pages/overview/OverviewPage";
import SourcesPage from "../pages/sources/SourcesPage";
import { AuthGate } from "./AuthGate";
import { Layout } from "./Layout";
import { visibleNav } from "./nav";
import { PlaceholderPage } from "./PlaceholderPage";

/** /dev/components exists in `npm run dev` and mock mode, never in the Caddy build. */
const SHOW_DEV = import.meta.env.DEV || import.meta.env.MODE === "mock";

function AppRoutes({ me }: { me: Me }) {
  const placeholders = visibleNav(me).filter((item) => item.phase);
  return (
    <Routes>
      <Route element={<Layout me={me} />}>
        <Route index element={<OverviewPage />} />
        <Route path="sources" element={<SourcesPage />} />
        <Route path="onboard" element={<OnboardPage />} />
        <Route path="contracts" element={<ContractsPage />} />
        <Route path="contracts/:id" element={<ContractDetailPage />} />
        <Route path="drift" element={<DriftInboxPage />} />
        <Route path="drift/:id" element={<DriftDetailPage />} />
        <Route path="lineage" element={<LineagePage />} />
        <Route path="lineage/:uid" element={<EventDetailView />} />
        <Route path="evidence" element={<EvidencePage />} />
        <Route path="delivery" element={<DeliveryPage />} />
        <Route path="audit" element={<AuditPage />} />
        {/* The demo panel is demo-machine only. The nav hides it, and so does the router:
            without this, typing /demo reaches it on a non-demo deployment. */}
        {me.demo_mode ? <Route path="demo" element={<DemoPage />} /> : null}
        {placeholders.map((item) => (
          <Route
            key={item.to}
            path={`${item.to.slice(1)}/*`}
            element={<PlaceholderPage labelKey={item.labelKey} phase={item.phase ?? ""} />}
          />
        ))}
        {SHOW_DEV ? <Route path="dev/components" element={<ComponentsPage />} /> : null}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export function AppShell() {
  return <AuthGate>{(me) => <AppRoutes me={me} />}</AuthGate>;
}
