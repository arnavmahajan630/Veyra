import { Outlet } from "react-router";
import type { Me } from "../api/types";
import { HotkeySheet } from "../hotkeys/HotkeySheet";
import { Header } from "./Header";
import { RailNav } from "./RailNav";
import { visibleNav } from "./nav";
import { useLiveUpdates } from "./useLiveUpdates";
import { useStageHotkeys } from "../pages/demo/useStageHotkeys";

export function Layout({ me }: { me: Me }) {
  useLiveUpdates();
  // The demo's stage hotkeys live here, not on /demo, because the script promises they work
  // from any page and the presenter is rarely on the demo panel. Inert outside demo mode.
  useStageHotkeys();
  return (
    <div className="flex h-screen">
      <RailNav items={visibleNav(me)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header me={me} />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="max-w-[1600px]">
            <Outlet />
          </div>
        </main>
      </div>
      <HotkeySheet />
    </div>
  );
}
