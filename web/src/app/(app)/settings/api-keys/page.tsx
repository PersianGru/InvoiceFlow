"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Textarea } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ApiError, API_URL, del, get, post, type ApiKeyRow } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { API_SCOPES } from "@/lib/integrations";
import { useFetch } from "@/lib/use-fetch";

export default function ApiKeysPage() {
  const { t, locale, license, refreshLicense } = useApp();
  const keys = useFetch<ApiKeyRow[]>(() => get<ApiKeyRow[]>("/api/v1/api-keys"), []);
  const [form, setForm] = useState({ name: "", scopes: ["documents:read", "documents:write", "exports:read"] as string[], rate_limit_per_minute: 120 });
  const [created, setCreated] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const apiOn = license?.features?.api === true;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setFlash(null);
    try {
      const k = await post<ApiKeyRow & { key: string }>("/api/v1/api-keys", form);
      setCreated(k.key);
      setForm({ ...form, name: "" });
      await keys.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    }
  }

  async function revoke(id: string) {
    if (!window.confirm(t("apikeys.revoke.confirm"))) return;
    try {
      await del(`/api/v1/api-keys/${id}`);
      await keys.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    }
  }

  function toggleScope(scope: string) {
    setForm((f) => ({ ...f, scopes: f.scopes.includes(scope) ? f.scopes.filter((s) => s !== scope) : [...f.scopes, scope] }));
  }

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <h1 className="mb-1 text-xl font-semibold">{t("apikeys.title")}</h1>
        <p className="mb-3 text-sm text-neutral-600">
          {t("apikeys.help")}{" "}
          <a className="underline" href={`${API_URL}/api/docs`} target="_blank" rel="noreferrer">
            {t("apikeys.docs")}
          </a>
        </p>
        {flash ? (
          <Notice tone={flash.tone} role="alert" className="mb-3">
            {flash.text}
          </Notice>
        ) : null}
        {created ? (
          <Notice tone="success" className="mb-3">
            <p className="mb-1 font-medium">{t("apikeys.created")}</p>
            <Textarea readOnly rows={2} className="font-mono text-xs" value={created} onFocus={(e) => e.currentTarget.select()} />
            <div className="mt-2 flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => navigator.clipboard.writeText(created)}>
                {t("common.copy")}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setCreated(null)}>
                {t("common.close")}
              </Button>
            </div>
          </Notice>
        ) : null}
        <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              <tr>
                <th className="p-2">{t("common.name")}</th>
                <th className="p-2">{t("license.key_hint")}</th>
                <th className="p-2">{t("apikeys.scopes")}</th>
                <th className="p-2">{t("apikeys.rate_limit")}</th>
                <th className="p-2">{t("apikeys.last_used")}</th>
                <th className="p-2" />
              </tr>
            </thead>
            <tbody>
              {(keys.data ?? []).map((k) => (
                <tr key={k.id} className="border-t border-neutral-100 dark:border-neutral-800">
                  <td className="p-2 font-medium">{k.name}</td>
                  <td className="p-2 font-mono text-xs">{k.key_hint}</td>
                  <td className="p-2">
                    {k.scopes.map((s) => (
                      <Badge key={s} className="mr-1">
                        {s}
                      </Badge>
                    ))}
                  </td>
                  <td className="p-2 tabular-nums">{k.rate_limit_per_minute}</td>
                  <td className="whitespace-nowrap p-2">{k.last_used_at ? formatDateTime(locale, k.last_used_at) : t("common.none")}</td>
                  <td className="p-2 text-right">
                    {k.revoked_at ? (
                      <Badge tone="neutral">{t("apikeys.revoked")}</Badge>
                    ) : (
                      <Button size="sm" variant="danger" onClick={() => revoke(k.id)}>
                        {t("apikeys.revoke")}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
              {keys.data && keys.data.length === 0 ? (
                <tr>
                  <td className="p-6 text-center text-neutral-600" colSpan={6}>
                    {t("apikeys.empty")}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
      <Card title={t("apikeys.new")}>
        {license && !apiOn ? (
          <Notice tone="warning" className="mb-3">
            {t("license.entitlements")}: API –
          </Notice>
        ) : null}
        <form onSubmit={submit}>
          <Field id="k-name" label={t("common.name")}>
            <Input id="k-name" required maxLength={120} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <fieldset className="mb-3">
            <legend className="mb-1 text-sm font-medium">{t("apikeys.scopes")}</legend>
            {API_SCOPES.map((s) => (
              <label key={s} className="flex items-center gap-2 py-0.5 text-sm">
                <input type="checkbox" checked={form.scopes.includes(s)} onChange={() => toggleScope(s)} />
                <span className="font-mono text-xs">{s}</span>
                <span className="text-neutral-600">{t(`apikeys.scope.${s}` as "apikeys.scope.documents:read")}</span>
              </label>
            ))}
          </fieldset>
          <Field id="k-rate" label={t("apikeys.rate_limit")}>
            <Input id="k-rate" type="number" min={1} max={6000} value={form.rate_limit_per_minute} onChange={(e) => setForm({ ...form, rate_limit_per_minute: Number(e.target.value) || 120 })} />
          </Field>
          <Button type="submit" disabled={!form.scopes.length}>
            {t("apikeys.new")}
          </Button>
        </form>
      </Card>
    </div>
  );
}
