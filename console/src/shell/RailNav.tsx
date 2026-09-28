import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router";
import { useI18n } from "../i18n/i18n";
import type { NavItem } from "./nav";

const STORAGE_KEY = "veyra.nav";

/** Below this width (a 1280×720 or 1024×768 projector) the rail starts collapsed, so pages keep their width. */
export const WIDE_SCREEN_PX = 1280;

function rememberedExpanded(): boolean {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(STORAGE_KEY);
  } catch {
    // Storage unavailable: fall back to the screen width.
  }
  if (stored) return stored !== "collapsed";
  return typeof window === "undefined" || window.innerWidth >= WIDE_SCREEN_PX;
}

export function RailNav({ items }: { items: readonly NavItem[] }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(rememberedExpanded);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    try {
      localStorage.setItem(STORAGE_KEY, next ? "expanded" : "collapsed");
    } catch {
      // Storage unavailable: the choice holds for this tab.
    }
  };

  return (
    <nav
      aria-label={t("nav.label")}
      className={`flex shrink-0 flex-col border-r border-rule bg-paper ${expanded ? "w-52" : "w-16"}`}
    >
      <ul className="flex flex-1 flex-col gap-0.5 py-3">
        {items.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.to === "/"}
              title={expanded ? undefined : t(item.labelKey)}
              className={({ isActive }) =>
                `mx-2 flex h-9 items-center gap-3 rounded-control px-3 ${
                  isActive ? "bg-thread text-paper" : "text-ink hover:bg-rule/40"
                }`
              }
            >
              <item.icon aria-hidden="true" size={18} strokeWidth={1.75} className="shrink-0" />
              <span className={expanded ? "truncate" : "sr-only"}>{t(item.labelKey)}</span>
            </NavLink>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        className="m-2 flex h-9 items-center gap-3 rounded-control px-3 text-ink-2 hover:text-ink"
      >
        {expanded ? (
          <PanelLeftClose aria-hidden="true" size={18} strokeWidth={1.75} className="shrink-0" />
        ) : (
          <PanelLeftOpen aria-hidden="true" size={18} strokeWidth={1.75} className="shrink-0" />
        )}
        <span className={expanded ? "" : "sr-only"}>{t(expanded ? "nav.collapse" : "nav.expand")}</span>
      </button>
    </nav>
  );
}
