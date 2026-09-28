import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { useLogin } from "../api/queries";
import { useI18n } from "../i18n/i18n";

export function LoginPage() {
  const { t } = useI18n();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate({ email, password });
  };

  const failure = login.error;
  return (
    <main className="flex min-h-screen items-start bg-paper px-6 pt-[18vh]">
      <form onSubmit={onSubmit} className="ml-[12vw] flex w-80 flex-col gap-4">
        <h1 className="text-title font-semibold">{t("login.title")}</h1>
        <label className="flex flex-col gap-1 text-meta text-ink-2">
          {t("login.email")}
          <input
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="rounded-control border border-rule bg-paper px-3 py-2 text-body text-ink"
          />
        </label>
        <label className="flex flex-col gap-1 text-meta text-ink-2">
          {t("login.password")}
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded-control border border-rule bg-paper px-3 py-2 text-body text-ink"
          />
        </label>
        {failure ? (
          <p role="alert" className="border-l-4 border-tier4 pl-3">
            {failure instanceof ApiError && failure.status === 401
              ? t("login.failed")
              : t("common.error", { message: failure.message })}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={login.isPending}
          className="rounded-control bg-thread px-3 py-2 text-paper disabled:opacity-60"
        >
          {t("login.submit")}
        </button>
      </form>
    </main>
  );
}
