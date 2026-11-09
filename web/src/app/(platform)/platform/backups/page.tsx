"use client";
// Platform backups: the platform backup, a backup of any firm, and restoring a firm from an
// uploaded archive (another server, or a firm shown as "missing data" after a platform restore).
import { useState, type FormEvent } from "react";

import { BackupsPanel } from "@/components/backups-panel";
import type { SectionView } from "@/components/settings-section";
import { Button, Card, Field, Input, Notice, Select, Spinner, Textarea } from "@/components/ui";
import { ApiError, get, post, upload, type PlatformOverview } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

export default function PlatformBackupsPage() {
  const { t } = useApp();
  const settings = useFetch<{ sections: Record<string, SectionView> }>(() => get("/api/v1/platform/settings"), []);
  const ov = useFetch<PlatformOverview>(() => get<PlatformOverview>("/api/v1/platform/overview"), []);
  const [form, setForm] = useState({ firm_id: "", password: "", recovery_key: "", reason: "" });
  const [file, setFile] = useState<File | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function restore(e: FormEvent) {
    e.preventDefault();
    if (!file || !form.firm_id) return;
    setBusy(true);
    setFlash(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("reason", form.reason);
    if (form.password) fd.append("password", form.password);
    if (form.recovery_key) fd.append("recovery_key", form.recovery_key);
    try {
      const r = await upload<{ status: string; error: string | null }>(`/api/v1/platform/firms/${form.firm_id}/restore`, fd);
      setFlash({ tone: r.status === "failed" ? "danger" : "success", text: r.status === "failed" ? (r.error ?? t("common.error")) : t("backups.restore_started") });
      await ov.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{t("platform.nav.backups")}</h1>
      {settings.data ? <BackupsPanel scope="platform" section={settings.data.sections.backup} onSectionSaved={settings.reload} /> : <Spinner label={t("common.loading")} />}
      <Card title={t("backups.firm_backups")}>
        <ul className="space-y-2 text-sm">
          {(ov.data?.firms ?? []).map((f) => (
            <li key={f.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{f.name}</span>
              <span className="text-xs text-neutral-600">{f.last_backup_at ?? t("platform.firm.no_backup")}</span>
              {f.is_active ? (
                <Button size="sm" variant="secondary" disabled={busy} onClick={async () => {
                  try {
                    await post(`/api/v1/platform/firms/${f.id}/backup`);
                    await ov.reload();
                  } catch (err) {
                    setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
                  }
                }}>
                  {t("backups.now")}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      </Card>
      <Card title={t("backups.restore_firm")}>
        <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">{t("backups.restore_firm_intro")}</p>
        {flash ? (
          <Notice tone={flash.tone} className="mb-3">
            {flash.text}
          </Notice>
        ) : null}
        <form onSubmit={restore} className="grid gap-x-4 md:grid-cols-2">
          <Field id="r-firm" label={t("platform.firm.name")}>
            <Select id="r-firm" required value={form.firm_id} onChange={(e) => setForm({ ...form, firm_id: e.target.value })}>
              <option value="">—</option>
              {(ov.data?.firms ?? []).map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                  {f.is_active ? "" : ` (${t("backups.missing_data")})`}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="r-file" label={t("backups.file")}>
            <Input id="r-file" type="file" accept=".age" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Field>
          <Field id="r-pw" label={t("backups.password")}>
            <Input id="r-pw" type="password" autoComplete="off" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </Field>
          <Field id="r-key" label={t("backups.recovery_key_input")}>
            <Input id="r-key" type="password" autoComplete="off" value={form.recovery_key} onChange={(e) => setForm({ ...form, recovery_key: e.target.value })} />
          </Field>
          <div className="md:col-span-2">
            <Field id="r-reason" label={t("reason.label")}>
              <Textarea id="r-reason" rows={2} required minLength={3} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
            </Field>
            <Button type="submit" variant="danger" disabled={busy || !file || !form.firm_id || form.reason.length < 3 || (!form.password && !form.recovery_key)}>
              {t("backups.restore")}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
