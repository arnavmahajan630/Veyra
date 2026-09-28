import * as Dialog from "@radix-ui/react-dialog";
import { useState, useSyncExternalStore } from "react";
import { Kbd } from "../components/Kbd";
import { useI18n } from "../i18n/i18n";
import { listHotkeys, subscribeHotkeys } from "./registry";
import { useHotkey } from "./useHotkey";

export function HotkeySheet() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const hotkeys = useSyncExternalStore(subscribeHotkeys, listHotkeys, listHotkeys);
  useHotkey({ combo: "?", description: t("hotkeys.help"), handler: () => setOpen((current) => !current) });

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-ink/20" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed left-1/2 top-24 w-[min(480px,92vw)] -translate-x-1/2 rounded-control border border-rule bg-paper p-5"
        >
          <Dialog.Title className="text-lead font-semibold">{t("hotkeys.title")}</Dialog.Title>
          <table className="mt-3 w-full border-collapse">
            <tbody>
              {hotkeys.map((hotkey) => (
                <tr key={`${hotkey.combo} ${hotkey.description}`} className="border-b border-rule">
                  <td className="py-1.5 pr-4">
                    <Kbd>{hotkey.combo}</Kbd>
                  </td>
                  <td className="py-1.5">{hotkey.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
