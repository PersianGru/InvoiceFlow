"use client";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { Button, Field, Input, Notice } from "@/components/ui";
import { ApiError, post } from "@/lib/api";
import { useApp } from "@/lib/app-context";

export default function ForgotPasswordPage() {
  const { t } = useApp();
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await post<{ message: string }>("/api/v1/auth/forgot-password", { email });
      setMsg({ tone: "success", text: r.message });
    } catch (err) {
      setMsg({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <h1 className="mb-2 text-2xl font-semibold">{t("forgot.title")}</h1>
      <p className="mb-4 text-sm text-neutral-600 dark:text-neutral-400">{t("forgot.intro")}</p>
      {msg ? (
        <Notice tone={msg.tone} className="mb-3">
          {msg.text}
        </Notice>
      ) : null}
      <form onSubmit={submit} noValidate>
        <Field id="email" label={t("login.email")}>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Button type="submit" disabled={busy || !email} className="w-full justify-center">
          {t("forgot.submit")}
        </Button>
      </form>
      <p className="mt-6 text-sm">
        <Link className="underline" href="/login">
          {t("login.submit")}
        </Link>
      </p>
    </main>
  );
}
