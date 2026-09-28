import { Outlet } from "react-router";
import type { Me } from "../api/types";
import { HotkeySheet } from "../hotkeys/HotkeySheet";
import { Header } from "./Header";
import { RailNav } from "./RailNav";
import { visibleNav } from "./nav";
import { useLiveUpdates } from "./useLiveUpdates";

export function Layout({ me }: { me: Me }) {
  useLiveUpdates();
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
