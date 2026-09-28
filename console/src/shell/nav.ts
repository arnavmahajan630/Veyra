import {
  CirclePlus,
  ClipboardList,
  FileText,
  GitBranch,
  LayoutDashboard,
  type LucideIcon,
  Presentation,
  Radio,
  Send,
  ShieldCheck,
  Spline,
} from "lucide-react";
import type { Me, Role } from "../api/types";

export interface NavItem {
  to: string;
  labelKey: string;
  icon: LucideIcon;
  /** Roles that see the entry; everyone when absent. */
  roles?: readonly Role[];
  /** Only in demo mode (B7's /demo). */
  demoOnly?: boolean;
  /** The phase that builds this page; until then it is a placeholder. */
  phase?: string;
}

export const NAV: readonly NavItem[] = [
  { to: "/", labelKey: "nav.overview", icon: LayoutDashboard },
  { to: "/sources", labelKey: "nav.sources", icon: Radio },
  { to: "/onboard", labelKey: "nav.onboard", icon: CirclePlus, roles: ["admin", "pack_author"] },
  { to: "/contracts", labelKey: "nav.contracts", icon: FileText },
  { to: "/drift", labelKey: "nav.drift", icon: GitBranch },
  { to: "/lineage", labelKey: "nav.lineage", icon: Spline, phase: "B6" },
  { to: "/evidence", labelKey: "nav.evidence", icon: ShieldCheck, phase: "B6" },
  { to: "/delivery", labelKey: "nav.delivery", icon: Send },
  { to: "/audit", labelKey: "nav.audit", icon: ClipboardList },
  { to: "/demo", labelKey: "nav.demo", icon: Presentation, demoOnly: true, phase: "B7" },
];

export function visibleNav(me: Me): NavItem[] {
  return NAV.filter(
    (item) => (!item.roles || item.roles.includes(me.role)) && (!item.demoOnly || me.demo_mode),
  );
}
