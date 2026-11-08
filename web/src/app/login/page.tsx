"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { ApiError, post, type User } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { Button, Field, Input, Notice } from "@/components/ui";

function LoginForm() {
  const { t, refreshSession } = useApp();
  const router = useRouter();
  const params = useSearchParams();
  const ssoError = params.get("sso");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await post<User>("/api/v1/auth/login", { email, password, totp_code: totp || undefined });
      await refreshSession();
      router.replace(me.role === "client_admin" || me.role === "client_user" ? "/my" : "/inbox");
    } catch (err) {
      if (err instanceof ApiError && err.code === "totp_required") {
        setNeedTotp(true);
        setError(t("login.totp_required"));
      } else {
        setError(err instanceof ApiError ? err.message : t("login.failed"));
      }
    } finally {
      setBusy(false);
    }
  }

  async function sso() {
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ authorization_url: string }>("/api/v1/auth/sso/start", { email, redirect_to: "/inbox" });
      window.location.assign(r.authorization_url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("login.failed"));
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <h1 className="mb-6 text-2xl font-semibold">{t("login.title")}</h1>
      {ssoError ? (
        <Notice tone="danger" role="alert" className="mb-3">
          {t("login.sso_failed", { reason: ssoError })}
        </Notice>
      ) : null}
      <form onSubmit={submit} noValidate>
        <Field id="email" label={t("login.email")}>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field id="password" label={t("login.password")}>
          <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {needTotp ? (
          <Field id="totp" label={t("login.totp")}>
            <Input id="totp" inputMode="numeric" autoComplete="one-time-code" value={totp} onChange={(e) => setTotp(e.target.value)} />
          </Field>
        ) : null}
        {error ? (
          <Notice tone="danger" role="alert" className="mb-3">
            {error}
          </Notice>
        ) : null}
        <Button type="submit" disabled={busy} className="w-full justify-center">
          {t("login.submit")}
        </Button>
        <Button type="button" variant="secondary" disabled={busy || !email} className="mt-2 w-full justify-center" onClick={sso}>
          {t("login.sso")}
        </Button>
      </form>
      <p className="mt-6 text-sm">
        <Link className="underline" href="/signup">
          {t("login.signup")}
        </Link>
      </p>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
