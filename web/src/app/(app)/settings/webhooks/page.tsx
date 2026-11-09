"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Textarea } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ClientPicker } from "@/components/client-picker";
import { ApiError, del, get, patch, post, type ClientCompany, type WebhookDelivery, type WebhookEndpoint } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { WEBHOOK_EVENTS, signatureExample } from "@/lib/integrations";
import { useFetch } from "@/lib/use-fetch";

function deliveryTone(status: WebhookDelivery["status"]): "success" | "warning" | "danger" {
  return status === "delivered" ? "success" : status === "failed" ? "danger" : "warning";
}

export default function WebhooksPage() {
  const { t, locale, refreshLicense } = useApp();
  const endpoints = useFetch<WebhookEndpoint[]>(() => get<WebhookEndpoint[]>("/api/v1/webhooks"), []);
  const [form, setForm] = useState({ url: "", description: "", events: [...WEBHOOK_EVENTS] as string[], client_company_ids: [] as string[] });
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const [secret, setSecret] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const deliveries = useFetch<WebhookDelivery[]>(() => (open ? get<WebhookDelivery[]>(`/api/v1/webhooks/${open}/deliveries`) : Promise.resolve([])), [open]);

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setFlash(null);
    try {
      await fn();
      if (ok) setFlash({ tone: "success", text: ok });
      await endpoints.reload();
      if (open) await deliveries.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      const ep = await post<WebhookEndpoint & { secret: string }>("/api/v1/webhooks", form);
      setSecret(ep.secret);
      setForm({ ...form, url: "", description: "" });
    });
  }

  function toggleEvent(ev: string) {
    setForm((f) => ({ ...f, events: f.events.includes(ev) ? f.events.filter((x) => x !== ev) : [...f.events, ev] }));
  }

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <h1 className="mb-1 text-xl font-semibold">{t("webhooks.title")}</h1>
        <p className="mb-3 text-sm text-neutral-600">
          {t("webhooks.help")} <code className="text-xs">{signatureExample()}</code>
        </p>
        {flash ? (
          <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"} className="mb-3">
            {flash.text}
          </Notice>
        ) : null}
        {secret ? (
          <Notice tone="success" className="mb-3">
            <p className="mb-1 font-medium">{t("webhooks.created")}</p>
            <Textarea readOnly rows={2} className="font-mono text-xs" value={secret} onFocus={(e) => e.currentTarget.select()} />
            <div className="mt-2 flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => navigator.clipboard.writeText(secret)}>
                {t("common.copy")}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setSecret(null)}>
                {t("common.close")}
              </Button>
            </div>
          </Notice>
        ) : null}
        <ul className="space-y-3">
          {(endpoints.data ?? []).map((ep) => (
            <li key={ep.id} className="rounded-md border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900">
              <div className="flex flex-wrap items-center gap-2">
                <code className="text-sm">{ep.url}</code>
                {ep.active ? <Badge tone="success">{t("webhooks.enable")}</Badge> : <Badge tone="danger">{t(`webhooks.disabled.${ep.disabled_reason ?? "by_user"}` as "webhooks.disabled.by_user")}</Badge>}
                {ep.consecutive_failures > 0 ? <Badge tone="warning">{ep.consecutive_failures} ✗</Badge> : null}
                <span className="ml-auto flex flex-wrap gap-1">
                  <Button size="sm" variant="secondary" onClick={() => run(() => post(`/api/v1/webhooks/${ep.id}/test`), t("webhooks.tested"))}>
                    {t("webhooks.test")}
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => run(async () => setSecret((await post<{ secret: string }>(`/api/v1/webhooks/${ep.id}/rotate-secret`)).secret))}>
                    {t("webhooks.rotate")}
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => run(() => patch(`/api/v1/webhooks/${ep.id}`, { url: ep.url, description: ep.description, events: ep.events, active: !ep.active, client_company_ids: ep.client_company_ids ?? [] }))}>
                    {ep.active ? t("webhooks.disable") : t("webhooks.enable")}
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => setOpen(open === ep.id ? null : ep.id)}>
                    {t("webhooks.deliveries")}
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => {
                      if (window.confirm(t("common.confirm_delete"))) void run(() => del(`/api/v1/webhooks/${ep.id}`));
                    }}
                  >
                    {t("common.delete")}
                  </Button>
                </span>
              </div>
              <p className="mt-1 text-xs text-neutral-600">
                {ep.description ? `${ep.description} · ` : ""}
                {ep.events.map((ev) => t(`webhooks.event.${ev}` as "webhooks.event.invoice.approved")).join(", ")}
              </p>
              {open === ep.id ? (
                <table className="mt-2 w-full text-xs">
                  <thead className="text-left uppercase text-neutral-600">
                    <tr>
                      <th className="p-1">{t("common.created")}</th>
                      <th className="p-1">{t("webhooks.events")}</th>
                      <th className="p-1">{t("common.status")}</th>
                      <th className="p-1">{t("webhooks.attempts")}</th>
                      <th className="p-1">{t("webhooks.next_attempt")}</th>
                      <th className="p-1" />
                    </tr>
                  </thead>
                  <tbody>
                    {(deliveries.data ?? []).map((d) => (
                      <tr key={d.id} className="border-t border-neutral-100 dark:border-neutral-800">
                        <td className="whitespace-nowrap p-1">{formatDateTime(locale, d.created_at)}</td>
                        <td className="p-1">{d.event_type}</td>
                        <td className="p-1">
                          <Badge tone={deliveryTone(d.status)}>{t(`delivery.${d.status}` as "delivery.pending")}</Badge>{" "}
                          {d.last_status_code ?? d.last_error ?? ""}
                        </td>
                        <td className="p-1 tabular-nums">{d.attempts}</td>
                        <td className="whitespace-nowrap p-1">{d.status === "pending" ? formatDateTime(locale, d.next_attempt_at) : "—"}</td>
                        <td className="p-1 text-right">
                          {d.status !== "pending" ? (
                            <Button size="sm" variant="secondary" onClick={() => run(() => post(`/api/v1/webhooks/${ep.id}/deliveries/${d.id}/retry`))}>
                              {t("webhooks.retry")}
                            </Button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </li>
          ))}
          {endpoints.data && endpoints.data.length === 0 ? <li className="text-sm text-neutral-600">{t("webhooks.empty")}</li> : null}
        </ul>
      </div>
      <Card title={t("webhooks.new")}>
        <form onSubmit={submit}>
          <Field id="w-url" label={t("webhooks.url")}>
            <Input id="w-url" type="url" required placeholder="https://" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
          </Field>
          <Field id="w-desc" label={t("webhooks.description")}>
            <Input id="w-desc" maxLength={200} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Field>
          <fieldset className="mb-3">
            <legend className="mb-1 text-sm font-medium">{t("webhooks.events")}</legend>
            {WEBHOOK_EVENTS.map((ev) => (
              <label key={ev} className="flex items-center gap-2 py-0.5 text-sm">
                <input type="checkbox" checked={form.events.includes(ev)} onChange={() => toggleEvent(ev)} />
                <span className="font-mono text-xs">{ev}</span>
                <span className="text-neutral-600">{t(`webhooks.event.${ev}` as "webhooks.event.invoice.approved")}</span>
              </label>
            ))}
          </fieldset>
          <ClientPicker id="w-clients" clients={clients.data ?? []} value={form.client_company_ids} onChange={(v) => setForm({ ...form, client_company_ids: v })} />
          <Button type="submit" disabled={!form.events.length}>
            {t("webhooks.new")}
          </Button>
        </form>
      </Card>
    </div>
  );
}
