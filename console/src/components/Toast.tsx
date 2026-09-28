import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

export const TOAST_MS = 5_000;

export type ToastTone = "info" | "success" | "warning";
type Push = (message: string, tone?: ToastTone) => void;

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

const TONE_BORDER: Record<ToastTone, string> = {
  info: "border-l-thread",
  success: "border-l-tier1",
  warning: "border-l-tier2",
};

const ToastContext = createContext<Push | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(0);

  const push = useCallback<Push>((message, tone = "info") => {
    const id = nextId.current++;
    setItems((current) => [...current, { id, message, tone }]);
    setTimeout(() => setItems((current) => current.filter((item) => item.id !== id)), TOAST_MS);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div role="status" aria-live="polite" className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
        {items.map((item) => (
          <div key={item.id} className={`border border-l-4 border-rule bg-paper px-4 py-2 ${TONE_BORDER[item.tone]}`}>
            {item.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): Push {
  const push = useContext(ToastContext);
  if (!push) throw new Error("useToast() needs a <ToastProvider> above it");
  return push;
}
