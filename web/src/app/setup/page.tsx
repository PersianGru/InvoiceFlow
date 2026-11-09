"use client";
// First run: the platform admin is created with the one-time setup code printed in the
// server log. Afterwards this page only says that the installation is set up.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Button, Field, Input, Notice, Spinner } from "@/components/ui";
import { ApiError, get, post, upload, type User } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { homeFor } from "@/lib/countries";

export default function SetupPage() {
  const { t, refreshSession } = useApp();
  const router = useRouter();
  const [required, setRequired] = useState<boolean | null>(null);
  const [form, setForm] = useState({ setup_code: "", email: "", display_name: "", password: "", confirm: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"new" | "restore">("new");
  const [file, setFile] = useState<File | null>(null);
  const [secret, setSecret] = useState({ password: "", recovery_key: "" });
  const [restored, setRestored] = useState(false);

  async function restore(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    const fd = new FormData();
    fd.append("setup_code", form.setup_code);
    fd.append("file", file);
    if (secret.password) fd.append("password", secret.password);
    if (secret.recovery_key) fd.append("recovery_key", secret.recovery_key);
    try {
      await upload("/api/v1/setup/restore", fd);
      setRestored(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void get<{ setup_required: boolean }>("/api/v1/setup/status")
      .then((s) => setRequired(s.setup_required))
      .catch(() => setRequired(false));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (form.password !== form.confirm) {
      setError(t("password.mismatch"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const me = await post<User>("/api/v1/setup", { setup_code: form.setup_code, email: form.email, display_name: form.display_name, password: form.password });
      await refreshSession();
      router.replace(homeFor(me));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  if (required === null) {
    return (
      <main className="flex flex-1 items-center justify-center p-8">
        <Spinner label={t("common.loading")} />
      </main>
    );
  }
  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-4 py-12">
      <h1 className="mb-2 text-2xl font-semibold">{t("setup.title")}</h1>
      {!required ? (
        <Notice>
          {t("setup.done")}{" "}
          <Link className="underline" href="/login">
            {t("login.submit")}
          </Link>
        </Notice>
      ) : (
        <>
          <p className="mb-4 text-sm text-neutral-600 dark:text-neutral-400">{t("setup.intro")}</p>
          <div className="mb-4 flex gap-2 text-sm">
            <Button variant={mode === "new" ? "primary" : "secondary"} size="sm" onClick={() => setMode("new")}>
              {t("setup.mode_new")}
            </Button>
            <Button variant={mode === "restore" ? "primary" : "secondary"} size="sm" onClick={() => setMode("restore")}>
              {t("setup.mode_restore")}
            </Button>
          </div>
          {mode === "restore" ? (
            restored ? (
              <Notice tone="success">
                {t("setup.restored")}{" "}
                <Link className="underline" href="/login">
                  {t("login.submit")}
                </Link>
              </Notice>
            ) : (
              <form onSubmit={restore} noValidate>
                <Field id="rcode" label={t("setup.code")} hint={t("setup.code_hint")}>
                  <Input id="rcode" autoComplete="off" required value={form.setup_code} onChange={(e) => setForm({ ...form, setup_code: e.target.value })} />
                </Field>
                <Field id="rfile" label={t("backups.file")}>
                  <Input id="rfile" type="file" accept=".age" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                </Field>
                <Field id="rpw" label={t("backups.password")}>
                  <Input id="rpw" type="password" autoComplete="off" value={secret.password} onChange={(e) => setSecret({ ...secret, password: e.target.value })} />
                </Field>
                <Field id="rkey" label={t("backups.recovery_key_input")}>
                  <Input id="rkey" type="password" autoComplete="off" value={secret.recovery_key} onChange={(e) => setSecret({ ...secret, recovery_key: e.target.value })} />
                </Field>
                {error ? (
                  <Notice tone="danger" role="alert" className="mb-3">
                    {error}
                  </Notice>
                ) : null}
                <Button type="submit" disabled={busy || !file || (!secret.password && !secret.recovery_key)} className="w-full justify-center">
                  {t("setup.restore_submit")}
                </Button>
              </form>
            )
          ) : (
          <form onSubmit={submit} noValidate>
            <Field id="code" label={t("setup.code")} hint={t("setup.code_hint")}>
              <Input id="code" autoComplete="off" required value={form.setup_code} onChange={(e) => setForm({ ...form, setup_code: e.target.value })} />
            </Field>
            <Field id="email" label={t("login.email")}>
              <Input id="email" type="email" autoComplete="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </Field>
            <Field id="name" label={t("users.name")}>
              <Input id="name" autoComplete="name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            </Field>
            <Field id="pw" label={t("login.password")} hint={t("password.hint")}>
              <Input id="pw" type="password" autoComplete="new-password" required minLength={12} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </Field>
            <Field id="pw2" label={t("password.confirm")}>
              <Input id="pw2" type="password" autoComplete="new-password" required value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} />
            </Field>
            {error ? (
              <Notice tone="danger" role="alert" className="mb-3">
                {error}
              </Notice>
            ) : null}
            <Button type="submit" disabled={busy} className="w-full justify-center">
              {t("setup.submit")}
            </Button>
          </form>
          )}
        </>
      )}
    </main>
  );
}
