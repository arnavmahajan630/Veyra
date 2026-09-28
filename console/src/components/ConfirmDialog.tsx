import * as Dialog from "@radix-ui/react-dialog";
import { useI18n } from "../i18n/i18n";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}

export function ConfirmDialog({ open, title, body, confirmLabel, onConfirm, onCancel, busy }: ConfirmDialogProps) {
  const { t } = useI18n();
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-ink/20" />
        <Dialog.Content className="fixed left-1/2 top-1/3 w-[min(440px,92vw)] -translate-x-1/2 rounded-control border border-rule bg-paper p-5">
          <Dialog.Title className="text-lead font-semibold">{title}</Dialog.Title>
          <Dialog.Description className="mt-2 text-ink-2">{body}</Dialog.Description>
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" onClick={onCancel} className="rounded-control border border-rule px-3 py-1.5">
              {t("common.cancel")}
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={busy}
              className="rounded-control bg-thread px-3 py-1.5 text-paper disabled:opacity-60"
            >
              {confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
