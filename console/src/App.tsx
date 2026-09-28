import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { BrowserRouter } from "react-router";
import { ToastProvider } from "./components/Toast";
import { installHotkeyListener } from "./hotkeys/registry";
import { I18nProvider } from "./i18n/i18n";
import { AppShell } from "./shell/AppShell";
import { createQueryClient } from "./shell/queryClient";
import { TenantScopeProvider } from "./shell/tenant";

const client = createQueryClient();

export default function App() {
  useEffect(() => installHotkeyListener(window), []);
  return (
    <QueryClientProvider client={client}>
      <I18nProvider>
        <ToastProvider>
          <BrowserRouter>
            <TenantScopeProvider>
              <AppShell />
            </TenantScopeProvider>
          </BrowserRouter>
        </ToastProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}
