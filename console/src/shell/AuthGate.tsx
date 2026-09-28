import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import { useMe } from "../api/queries";
import type { Me } from "../api/types";
import { useI18n } from "../i18n/i18n";
import { LoginPage } from "./LoginPage";

/** Renders `children(me)` for a live session, the login page for a 401, an error otherwise. */
export function AuthGate({ children }: { children: (me: Me) => ReactNode }) {
  const { t } = useI18n();
  const me = useMe();

  if (me.isPending) return <p className="p-6 text-ink-2">{t("common.loading")}</p>;
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) return <LoginPage />;
    return (
      <p role="alert" className="p-6">
        {t("common.error", { message: me.error.message })}
      </p>
    );
  }
  return <>{children(me.data)}</>;
}
