import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { useI18n } from "../i18n/i18n";

export interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
}

export function Drawer({ open, onOpenChange, title, children }: DrawerProps) {
  const { t } = useI18n();
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-ink/20" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-y-0 right-0 flex w-[min(560px,100vw)] flex-col border-l border-rule bg-paper"
        >
          <header className="flex items-center justify-between border-b border-rule px-5 py-3">
            <Dialog.Title className="text-lead font-semibold">{title}</Dialog.Title>
            <Dialog.Close className="rounded-control px-2 py-1 text-meta text-ink-2 hover:text-ink">
              {t("common.close")}
            </Dialog.Close>
          </header>
          <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
