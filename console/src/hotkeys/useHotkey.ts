import { useEffect, useRef } from "react";
import { registerHotkey, type Hotkey } from "./registry";

/** Register a hotkey for the lifetime of the component; handler and `when` stay fresh. */
export function useHotkey(hotkey: Hotkey): void {
  const latest = useRef(hotkey);
  useEffect(() => {
    latest.current = hotkey;
  });
  const { combo, description } = hotkey;
  useEffect(
    () =>
      registerHotkey({
        combo,
        description,
        handler: (event) => latest.current.handler(event),
        when: () => latest.current.when?.() ?? true,
      }),
    [combo, description],
  );
}
