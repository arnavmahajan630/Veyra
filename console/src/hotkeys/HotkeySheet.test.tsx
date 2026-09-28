import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { I18nProvider } from "../i18n/i18n";
import { HotkeySheet } from "./HotkeySheet";
import { installHotkeyListener, registerHotkey } from "./registry";

const cleanups: Array<() => void> = [];
afterEach(() => {
  for (const undo of cleanups.splice(0)) undo();
});

describe("HotkeySheet", () => {
  it("opens on ? and lists every registered hotkey", async () => {
    cleanups.push(installHotkeyListener(window));
    cleanups.push(registerHotkey({ combo: "Shift+T", description: "Tamper a segment", handler: () => {} }));
    render(
      <I18nProvider>
        <HotkeySheet />
      </I18nProvider>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.keyboard("?");
    const sheet = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    expect(sheet).toHaveTextContent("Shift+T");
    expect(sheet).toHaveTextContent("Tamper a segment");
    expect(sheet).toHaveTextContent("Show keyboard shortcuts");
  });
});
