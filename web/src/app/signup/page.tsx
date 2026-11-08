"use client";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError, post } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { Button, Field, Input, Notice, Select } from "@/components/ui";

// Every EU member state (plus CH); the Baltic states first, then the rest alphabetically. Each has a
// VAT-rate entry, a retention period and (where one exists) an e-invoicing mandate in the rule packs.
const COUNTRIES = ["EE", "LV", "LT", "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LU", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"];

// Localised country name from the browser (Intl), falling back to the code.
function countryLabel(code: string, locale: string): string {
  try {
    const name = new Intl.DisplayNames([locale], { type: "region" }).of(code);
    return name && name !== code ? `${code} · ${name}` : code;
  } catch {
    return code;
  }
}

export default function SignupPage() {
  const { t, refreshSession, locale } = useApp();
  const router = useRouter();
  const [form, setForm] = useState({ firm_name: "", country: "DE", owner_email: "", owner_name: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await post("/api/v1/firms", { ...form, locale });
      await post("/api/v1/auth/login", { email: form.owner_email, password: form.password });
      await refreshSession();
      router.replace("/settings/license");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <h1 className="mb-6 text-2xl font-semibold">{t("signup.title")}</h1>
      <form onSubmit={submit} noValidate>
        <Field id="firm_name" label={t("signup.firm_name")}>
          <Input id="firm_name" required value={form.firm_name} onChange={set("firm_name")} />
        </Field>
        <Field id="country" label={t("signup.country")}>
          <Select id="country" value={form.country} onChange={set("country")}>
            {COUNTRIES.map((c) => (
              <option key={c} value={c}>
                {countryLabel(c, locale)}
              </option>
            ))}
          </Select>
        </Field>
        <Field id="owner_name" label={t("signup.owner_name")}>
          <Input id="owner_name" value={form.owner_name} onChange={set("owner_name")} />
        </Field>
        <Field id="owner_email" label={t("login.email")}>
          <Input id="owner_email" type="email" autoComplete="email" required value={form.owner_email} onChange={set("owner_email")} />
        </Field>
        <Field id="password" label={t("login.password")}>
          <Input id="password" type="password" autoComplete="new-password" required minLength={12} value={form.password} onChange={set("password")} />
        </Field>
        {error ? (
          <Notice tone="danger" role="alert" className="mb-3">
            {error}
          </Notice>
        ) : null}
        <Button type="submit" disabled={busy} className="w-full justify-center">
          {t("signup.submit")}
        </Button>
      </form>
    </main>
  );
}
