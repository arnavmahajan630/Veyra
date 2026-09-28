import { Navigate, Route, Routes } from "react-router";
import type { Me } from "../api/types";
import ComponentsPage from "../pages/dev/ComponentsPage";
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
