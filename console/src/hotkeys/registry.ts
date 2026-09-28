// One global hotkey registry (C5). Pages register combos; a single window listener
// dispatches them, and never while the user is typing into a form field.

export interface Hotkey {
  combo: string;
  description: string;
  handler: (event: KeyboardEvent) => void;
  when?: () => boolean;
}

const entries = new Map<number, Hotkey>();
const listeners = new Set<() => void>();
let nextId = 0;
let snapshot: readonly Hotkey[] = [];

function changed(): void {
  snapshot = [...entries.values()];
  for (const listener of listeners) listener();
}

export function registerHotkey(hotkey: Hotkey): () => void {
  const id = nextId++;
  entries.set(id, hotkey);
  changed();
  return () => {
    if (entries.delete(id)) changed();
  };
}

export function listHotkeys(): readonly Hotkey[] {
  return snapshot;
}

export function subscribeHotkeys(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function comboOf(event: KeyboardEvent): string {
  if (event.key === "?") return "?";
  let key = event.key.length === 1 ? event.key.toUpperCase() : event.key;
  if (event.code.startsWith("Digit")) key = event.code.slice("Digit".length);
  else if (event.code.startsWith("Key")) key = event.code.slice("Key".length);
  const parts: string[] = [];
  if (event.ctrlKey) parts.push("Ctrl");
  if (event.altKey) parts.push("Alt");
  if (event.metaKey) parts.push("Meta");
  if (event.shiftKey) parts.push("Shift");
  parts.push(key);
  return parts.join("+");
}

export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || target.getAttribute("contenteditable") === "true") return true;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

export function dispatchHotkey(event: KeyboardEvent): boolean {
  if (event.defaultPrevented || isTyping(event.target)) return false;
  const combo = comboOf(event);
  for (const hotkey of snapshot) {
    if (hotkey.combo === combo && (hotkey.when?.() ?? true)) {
      event.preventDefault();
      hotkey.handler(event);
      return true;
    }
  }
  return false;
}

export function installHotkeyListener(target: Window = window): () => void {
  const listener = (event: KeyboardEvent) => {
    dispatchHotkey(event);
  };
  target.addEventListener("keydown", listener);
  return () => target.removeEventListener("keydown", listener);
}
