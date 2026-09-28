import type { ReactNode } from "react";

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-control border border-rule bg-paper px-1.5 py-0.5 font-mono text-meta text-ink">
      {children}
    </kbd>
  );
}
