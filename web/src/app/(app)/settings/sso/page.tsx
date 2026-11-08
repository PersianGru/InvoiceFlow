"use client";
import { useState, type FormEvent } from "react";

import { Button, Card, Field, Input, Notice, Select } from "@/components/ui";
import { ApiError, get, put, type SsoConfig } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

export default function SsoPage() {
  const { t, firm, license, refreshLicense } = useApp();
  const firmId = firm?.id ?? "";
  const cfg = useFetch<SsoConfig>(() => get<SsoConfig>(`/api/v1/firms/${firmId}/sso`), [firmId]);
  const [form, setForm] = useState<{ enabled: boolean; issuer: string; client_id: string; client_secret: string; allowed_domains: string; provisioning: "invited_only" | "auto"; default_role: "accountant" | "junior_accountant" } | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const ssoOn = license?.features?.sso === true;
  const draft = form ?? (cfg.data ? { enabled: cfg.data.enabled, issuer: cfg.data.issuer, client_id: cfg.data.client_id, client_secret: "", allowed_domains: cfg.data.allowed_domains.join(", "), provisioning: cfg.data.provisioning as "invited_only" | "auto", default_role: cfg.data.default_role as "accountant" | "junior_accountant" } : null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!draft) return;
    setFlash(null);
    try {
      await put(`/api/v1/firms/${firmId}/sso`, { ...draft, client_secret: draft.client_secret || null, allowed_domains: draft.allowed_domains.split(",").map((d) => d.trim()).filter(Boolean) });
      setFlash({ tone: "success", text: t("sso.saved") });
      setForm(null);
      await cfg.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    }
  }

  if (!draft) return null;
  return (
    <div className="max-w-2xl">
      <h1 className="mb-1 text-xl font-semibold">{t("sso.title")}</h1>
      <p className="mb-3 text-sm text-neutral-600">{t("sso.help")}</p>
      {license && !ssoOn ? (
        <Notice tone="warning" className="mb-3">
          {t("sso.not_entitled")}
        </Notice>
      ) : null}
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      <Card>
        <form onSubmit={submit}>
          <label className="mb-3 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.enabled} onChange={(e) => setForm({ ...draft, enabled: e.target.checked })} />
            {t("sso.enabled")}
          </label>
          <Field id="sso-issuer" label={t("sso.issuer")} hint="https://login.example.com/realms/firm">
            <Input id="sso-issuer" value={draft.issuer} onChange={(e) => setForm({ ...draft, issuer: e.target.value })} />
          </Field>
          <Field id="sso-client" label={t("sso.client_id")}>
            <Input id="sso-client" value={draft.client_id} onChange={(e) => setForm({ ...draft, client_id: e.target.value })} />
          </Field>
          <Field id="sso-secret" label={t("sso.client_secret")} hint={cfg.data?.has_client_secret ? t("sso.secret_stored") : undefined}>
            <Input id="sso-secret" type="password" autoComplete="off" value={draft.client_secret} onChange={(e) => setForm({ ...draft, client_secret: e.target.value })} />
          </Field>
          <Field id="sso-domains" label={t("sso.domains")} hint="example.com, example.de">
            <Input id="sso-domains" value={draft.allowed_domains} onChange={(e) => setForm({ ...draft, allowed_domains: e.target.value })} />
          </Field>
          <Field id="sso-prov" label={t("sso.provisioning")}>
            <Select id="sso-prov" value={draft.provisioning} onChange={(e) => setForm({ ...draft, provisioning: e.target.value as "invited_only" | "auto" })}>
              <option value="invited_only">{t("sso.provisioning.invited_only")}</option>
              <option value="auto">{t("sso.provisioning.auto")}</option>
            </Select>
          </Field>
          {draft.provisioning === "auto" ? (
            <Field id="sso-role" label={t("sso.default_role")}>
              <Select id="sso-role" value={draft.default_role} onChange={(e) => setForm({ ...draft, default_role: e.target.value as "accountant" | "junior_accountant" })}>
                <option value="junior_accountant">{t("role.junior_accountant")}</option>
                <option value="accountant">{t("role.accountant")}</option>
              </Select>
            </Field>
          ) : null}
          <p className="mb-3 text-xs text-neutral-600">
            {t("sso.callback")}: <code>{cfg.data?.callback_url}</code>
          </p>
          <Button type="submit">{t("common.save")}</Button>
        </form>
      </Card>
    </div>
  );
}
