import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/mukta/400.css";
import "@fontsource/mukta/500.css";
import "@fontsource/mukta/600.css";
import "./design/tokens.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

// MSW's worker keeps its mocked tabs in memory. When the browser stops an idle worker (a
// backgrounded or sleeping tab), it restarts with no tabs and passes every request through to
// the network, which in mock mode means a dead proxy. Re-announcing this tab is idempotent.
const MOCK_REACTIVATE_MS = 3000;

function keepMockingThisTab(): void {
  const activate = () => navigator.serviceWorker.controller?.postMessage("MOCK_ACTIVATE");
  setInterval(activate, MOCK_REACTIVATE_MS);
  window.addEventListener("focus", activate);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") activate();
  });
}

async function startMocksInMockMode(): Promise<void> {
  if (import.meta.env.MODE !== "mock") return;
  const { worker } = await import("./mocks/browser");
  await worker.start({ onUnhandledRequest: "bypass" });
  keepMockingThisTab();
}

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

void startMocksInMockMode().then(() => {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
