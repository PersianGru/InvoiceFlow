"use client";
// Platform overview: firms with license state, users, last backup; health; create a firm;
// open a firm (switch into it) for setup and troubleshooting.
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useReason } from "@/components/reason-dialog";
import { Badge, Button, Card, Field, Input, Notice, Select, Spinner } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ApiError, get, post, type FirmCreated, type PlatformOverview } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { COUNTRIES, countryLabel } from "@/lib/countries";
import { useFetch } from "@/lib/use-fetch";

const STATE_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = { valid: "success", grace: "warning", expired: "danger", invalid: "danger", unlicensed: "neutral" };

export default function PlatformOverviewPage() {
  const { t, locale, refreshSession } = useApp();
  const router = useRouter();
  const ov = useFetch<PlatformOverview>(() => get<PlatformOverview>("/api/v1/platform/overview"), []);
  const [form, setForm] = useState({ name: "", country: "DE", owner_email: "", owner_name: "", license_key: "", send_email: true });
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [created, setCreated] = useState<FirmCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, ask] = useReason();

  if (!ov.data) return ov.error ? <Notice tone="danger">{ov.error}</Notice> : <Spinner label={t("common.loading")} />;
  const o = ov.data;
  const keyNeeded = o.firm_count >= 1;
  const full = o.firm_count >= o.max_firms;

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFlash(null);
    try {
      const r = await post<FirmCreated>("/api/v1/platform/firms", { ...form, license_key: form.license_key || null });
      setCreated(r);
      setForm({ ...form, name: "", owner_email: "", owner_name: "", license_key: "" });
      await ov.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    await post("/api/v1/platform/switch", { firm_id: id });
    await refreshSession();
    router.replace("/inbox");
  }

  async function firmAction(id: string, action: "deactivate" | "reactivate" | "delete") {
    const reason = await ask(t(`platform.firm.${action}`), { danger: action === "delete", body: action === "delete" ? t("platform.firm.delete_body") : undefined });
    if (!reason) return;
    try {
      await post(`/api/v1/platform/firms/${id}/${action}`, { reason });
      setFlash({ tone: "success", text: t("common.saved") });
      await ov.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    }
  }

  return (
    <div className="space-y-4">
      {dialog}
      <h1 className="text-xl font-semibold">{t("platform.overview")}</h1>
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"}>
          {flash.text}
        </Notice>
      ) : null}
      <div className="grid gap-4 md:grid-cols-3">
        <Card title={t("platform.firms_count")}>
          <p className="text-2xl font-semibold">
            {o.firm_count} / {o.max_firms >= 1000000 ? "∞" : o.max_firms}
          </p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">{t("platform.max_firms_hint")}</p>
        </Card>
        <Card title={t("platform.license")}>
          <Badge tone={STATE_TONE[o.platform_license_state] ?? "neutral"}>{o.platform_license_state}</Badge>
          <p className="mt-2 text-sm text-neutral-600 dark:text-neutral-400">{t("platform.license_hint")}</p>
        </Card>
        <Card title={t("platform.health")}>
          <ul className="space-y-1 text-sm">
            {Object.entries(o.health).map(([k, v]) => (
              <li key={k} className="flex justify-between gap-2">
                <span>{k}</span>
                <Badge tone={v === "ok" || v === "configured" || v === "set" || v === true ? "success" : "warning"}>{String(v)}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card title={t("platform.firms")}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-neutral-600 dark:text-neutral-300">
              <tr>
                <th className="p-2">{t("platform.firm.name")}</th>
                <th className="p-2">{t("platform.firm.license")}</th>
                <th className="p-2">{t("platform.firm.users")}</th>
                <th className="p-2">{t("platform.firm.domain")}</th>
                <th className="p-2">{t("platform.firm.backup")}</th>
                <th className="p-2">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {o.firms.map((f) => (
                <tr key={f.id} className="border-t border-neutral-100 align-top dark:border-neutral-800">
                  <td className="p-2">
                    <div className="font-medium">{f.name}</div>
                    <div className="text-xs text-neutral-600 dark:text-neutral-400">{f.installation_id_grouped}</div>
                    {!f.is_active ? <Badge tone="warning">{f.deleted_at ? t("platform.firm.deleted") : t("platform.firm.inactive")}</Badge> : null}
                  </td>
                  <td className="p-2">
                    <Badge tone={STATE_TONE[f.license_state] ?? "neutral"}>{f.license_state}</Badge>
                    {f.key_hint ? <span className="ml-1 text-xs">…{f.key_hint}</span> : null}
                  </td>
                  <td className="p-2">
                    {f.users} · {t("platform.firm.clients", { n: f.client_companies })}
                  </td>
                  <td className="p-2 text-xs">{f.inbound_domain ?? "—"}</td>
                  <td className="p-2 text-xs">{f.last_backup_at ? formatDateTime(locale, f.last_backup_at) : <Badge tone="warning">{t("platform.firm.no_backup")}</Badge>}</td>
                  <td className="p-2">
                    <div className="flex flex-wrap gap-1">
                      {f.is_active ? (
                        <Button size="sm" onClick={() => void open(f.id)}>
                          {t("platform.firm.open")}
                        </Button>
                      ) : null}
                      {f.is_active ? (
                        <Button size="sm" variant="secondary" onClick={() => void firmAction(f.id, "deactivate")}>
                          {t("platform.firm.deactivate")}
                        </Button>
                      ) : (
                        <Button size="sm" variant="secondary" onClick={() => void firmAction(f.id, "reactivate")}>
                          {t("platform.firm.reactivate")}
                        </Button>
                      )}
                      {!f.deleted_at ? (
                        <Button size="sm" variant="danger" onClick={() => void firmAction(f.id, "delete")}>
                          {t("platform.firm.delete")}
                        </Button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={t("platform.create_firm")}>
        {created ? (
          <Notice tone="success" className="mb-3">
            <p>{created.email_sent ? t("platform.created_sent", { email: created.owner.email }) : t("platform.created_link", { email: created.owner.email })}</p>
            <div className="mt-2 flex gap-2">
              <Input readOnly value={created.invite_link} aria-label={t("users.link")} onFocus={(e) => e.currentTarget.select()} />
              <Button variant="secondary" onClick={() => void navigator.clipboard?.writeText(created.invite_link)}>
                {t("common.copy")}
              </Button>
            </div>
          </Notice>
        ) : null}
        {full ? <Notice tone="warning">{t("platform.full", { n: o.max_firms })}</Notice> : null}
        <form onSubmit={create} className="grid gap-x-4 md:grid-cols-2">
          <Field id="f-name" label={t("platform.firm.name")}>
            <Input id="f-name" required minLength={2} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field id="f-country" label={t("signup.country")}>
            <Select id="f-country" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })}>
              {COUNTRIES.map((c) => (
                <option key={c} value={c}>
                  {countryLabel(c, locale)}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="f-email" label={t("platform.owner_email")}>
            <Input id="f-email" type="email" required value={form.owner_email} onChange={(e) => setForm({ ...form, owner_email: e.target.value })} />
          </Field>
          <Field id="f-owner" label={t("platform.owner_name")}>
            <Input id="f-owner" value={form.owner_name} onChange={(e) => setForm({ ...form, owner_name: e.target.value })} />
          </Field>
          <Field id="f-key" label={keyNeeded ? t("platform.license_key_required") : t("platform.license_key_optional")}>
            <Input id="f-key" required={keyNeeded} autoComplete="off" value={form.license_key} onChange={(e) => setForm({ ...form, license_key: e.target.value })} />
          </Field>
          <label className="mb-3 flex items-center gap-2 self-end text-sm">
            <input type="checkbox" checked={form.send_email} onChange={(e) => setForm({ ...form, send_email: e.target.checked })} />
            {t("users.send_email")}
          </label>
          <div className="md:col-span-2">
            <Button type="submit" disabled={busy || full}>
              {t("platform.create_firm")}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
