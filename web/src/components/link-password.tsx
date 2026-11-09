"use client";
// Set a password from a one-time link (invite or reset). The token is read from the URL fragment and
// sent in the request body only; it never appears in a URL the server sees.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button, Field, Input, Notice, Spinner } from "@/components/ui";
import { ApiError, post, type User } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { fragmentToken, homeFor } from "@/lib/countries";

interface LinkInfo {
  kind: "invite" | "reset";
  email: string;
  display_name: string;
  expires_at: string;
}

export function LinkPassword({ kind }: { kind: "invite" | "reset" }) {
  const { t, refreshSession } = useApp();
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [info, setInfo] = useState<LinkInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [pw, setPw] = useState({ password: "", confirm: "" });
  const [busy, setBusy] = useState(false);

  // The token is read once and kept here: the address bar is cleared right after, so a second run of
  // the effect (re-render of the app context, React strict mode) must not read the hash again.
  const tokenRef = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (tokenRef.current === undefined) {
      tokenRef.current = fragmentToken(window.location.hash);
      // drop the token from the address bar and the history
      if (tokenRef.current) window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    const tok = tokenRef.current;
    let live = true;
    const handle = setTimeout(() => {
      setToken(tok);
      if (!tok) {
        setInvalid(true);
        return;
      }
      void post<LinkInfo>("/api/v1/auth/link/inspect", { token: tok })
        .then((r) => {
          if (live) setInfo(r);
        })
        .catch((e) => {
          if (!live) return;
          if (e instanceof ApiError) setError(e.message);
          else setInvalid(true);
        });
    }, 0);
    return () => {
      live = false;
      clearTimeout(handle);
    };
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (pw.password !== pw.confirm) {
      setError(t("password.mismatch"));
      return;
    }
    setBusy(true);
    setError(null);
    setInvalid(false);
    try {
      const me = await post<User>("/api/v1/auth/link/complete", { token, password: pw.password });
      await refreshSession();
      router.replace(me.mfa_setup_required ? "/setup-2fa" : me.totp_enabled ? "/login" : homeFor(me));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  const shown = error ?? (invalid ? t("link.invalid") : null);
  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <h1 className="mb-2 text-2xl font-semibold">{kind === "invite" ? t("link.invite_title") : t("link.reset_title")}</h1>
      {info ? <p className="mb-4 text-sm text-neutral-600 dark:text-neutral-400">{t("link.for", { email: info.email })}</p> : null}
      {shown ? (
        <Notice tone="danger" role="alert" className="mb-3">
          {shown}{" "}
          <Link className="underline" href="/forgot-password">
            {t("login.forgot")}
          </Link>
        </Notice>
      ) : null}
      {!info && !shown ? <Spinner label={t("common.loading")} /> : null}
      {info ? (
        <form onSubmit={submit} noValidate>
          <Field id="pw" label={t("password.new")} hint={t("password.hint")}>
            <Input id="pw" type="password" autoComplete="new-password" required minLength={12} value={pw.password} onChange={(e) => setPw({ ...pw, password: e.target.value })} />
          </Field>
          <Field id="pw2" label={t("password.confirm")}>
            <Input id="pw2" type="password" autoComplete="new-password" required value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} />
          </Field>
          <Button type="submit" disabled={busy} className="w-full justify-center">
            {t("password.save")}
          </Button>
        </form>
      ) : null}
    </main>
  );
}
