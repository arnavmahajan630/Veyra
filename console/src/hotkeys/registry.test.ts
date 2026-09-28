import { afterEach, describe, expect, it, vi } from "vitest";
import {
  comboOf,
  dispatchHotkey,
  installHotkeyListener,
  listHotkeys,
  registerHotkey,
  subscribeHotkeys,
} from "./registry";

const cleanups: Array<() => void> = [];
afterEach(() => {
  for (const undo of cleanups.splice(0)) undo();
});

function keydown(init: KeyboardEventInit): KeyboardEvent {
  return new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
}

describe("comboOf", () => {
  it("spells combos from the physical key", () => {
    expect(comboOf(keydown({ key: "T", code: "KeyT", shiftKey: true }))).toBe("Shift+T");
    expect(comboOf(keydown({ key: "!", code: "Digit1", shiftKey: true }))).toBe("Shift+1");
    expect(comboOf(keydown({ key: "k", code: "KeyK", ctrlKey: true }))).toBe("Ctrl+K");
    expect(comboOf(keydown({ key: "?", code: "Slash", shiftKey: true }))).toBe("?");
  });
});

describe("the registry", () => {
  it("runs the matching handler and lists registered hotkeys", () => {
    const handler = vi.fn();
    cleanups.push(registerHotkey({ combo: "Shift+T", description: "Tamper", handler }));
    expect(listHotkeys().map((h) => h.description)).toEqual(["Tamper"]);
    expect(dispatchHotkey(keydown({ key: "T", code: "KeyT", shiftKey: true }))).toBe(true);
    expect(handler).toHaveBeenCalledOnce();
  });

  it("skips a hotkey whose `when` is false", () => {
    const handler = vi.fn();
    cleanups.push(registerHotkey({ combo: "?", description: "Help", handler, when: () => false }));
    expect(dispatchHotkey(keydown({ key: "?" }))).toBe(false);
    expect(handler).not.toHaveBeenCalled();
  });

  it("forgets a hotkey once unregistered and notifies subscribers", () => {
    const listener = vi.fn();
    cleanups.push(subscribeHotkeys(listener));
    const undo = registerHotkey({ combo: "Shift+R", description: "Reset", handler: vi.fn() });
    undo();
    expect(listHotkeys()).toEqual([]);
    expect(listener).toHaveBeenCalledTimes(2);
  });
});

describe("the global listener", () => {
  it("ignores keys typed into a form field", () => {
    const handler = vi.fn();
    cleanups.push(registerHotkey({ combo: "?", description: "Help", handler }));
    cleanups.push(installHotkeyListener(window));
    const input = document.createElement("input");
    document.body.append(input);
    cleanups.push(() => input.remove());

    input.dispatchEvent(keydown({ key: "?" }));
    expect(handler).not.toHaveBeenCalled();

    document.body.dispatchEvent(keydown({ key: "?" }));
    expect(handler).toHaveBeenCalledOnce();
  });
});
