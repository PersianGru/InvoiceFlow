"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Select, Spinner } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ApiError, get, imageUrl, post, type ClientCompany, type ExportRow, type ExportTemplate } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { EXPORT_FORMATS, formatBytes, templatesFor } from "@/lib/integrations";
import { useFetch } from "@/lib/use-fetch";

const STATUSES = ["approved", "needs_review", "needs_info", "rejected"] as const;

export default function ExportsPage() {
  const { t, locale, license, refreshLicense } = useApp();
  const exports = useFetch<ExportRow[]>(() => get<ExportRow[]>("/api/v1/exports", { limit: 100 }), []);
  const templates = useFetch<ExportTemplate[]>(() => get<ExportTemplate[]>("/api/v1/export-templates"), []);
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const [form, setForm] = useState({ format: "xlsx", template_id: "", status: "approved", client_company_id: "", date_from: "", date_to: "", datev_consultant_number: "", datev_client_number: "" });
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const paused = license !== null && !license.core_on;
  const clientName = new Map((clients.data ?? []).map((c) => [c.id, c.name]));
  const usable = templatesFor(templates.data ?? [], form.format, form.client_company_id);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFlash(null);
    try {
      const options: Record<string, string> = {};
      if (form.format === "datev") {
        if (form.datev_consultant_number) options.datev_consultant_number = form.datev_consultant_number;
        if (form.datev_client_number) options.datev_client_number = form.datev_client_number;
      }
      const row = await post<ExportRow>("/api/v1/exports", {
        format: form.format,
        template_id: form.template_id || null,
        filters: { status: form.status || null, client_company_id: form.client_company_id || null, date_from: form.date_from || null, date_to: form.date_to || null },
        options,
      });
      setFlash({ tone: "success", text: t("exports.created", { name: row.file_name, n: row.document_count }) });
      await exports.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <h1 className="mb-3 text-xl font-semibold">{t("exports.title")}</h1>
        {flash ? (
          <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"} className="mb-3">
            {flash.text}
          </Notice>
        ) : null}
        {exports.loading && !exports.data ? <Spinner label={t("common.loading")} /> : null}
        {exports.data ? (
          <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                <tr>
                  <th className="p-2">{t("common.created")}</th>
                  <th className="p-2">{t("common.format")}</th>
                  <th className="p-2">{t("inbox.col.file")}</th>
                  <th className="p-2 text-right">{t("exports.documents")}</th>
                  <th className="p-2 text-right">{t("exports.size")}</th>
                  <th className="p-2">{t("exports.filter.client")}</th>
                  <th className="p-2" />
                </tr>
              </thead>
              <tbody>
                {exports.data.map((e) => (
                  <tr key={e.id} className="border-t border-neutral-100 dark:border-neutral-800">
                    <td className="whitespace-nowrap p-2">{formatDateTime(locale, e.created_at)}</td>
                    <td className="p-2">
                      <Badge>{e.format}</Badge>
                    </td>
                    <td className="p-2 font-mono text-xs">{e.file_name}</td>
                    <td className="p-2 text-right tabular-nums">{e.document_count}</td>
                    <td className="p-2 text-right tabular-nums">{formatBytes(e.size_bytes)}</td>
                    <td className="p-2">{e.client_company_id ? (clientName.get(e.client_company_id) ?? "…") : t("common.none")}</td>
                    <td className="p-2 text-right">
                      <a className="text-sm underline-offset-2 hover:underline" href={imageUrl(e.download_url)}>
                        {t("common.download")}
                      </a>
                    </td>
                  </tr>
                ))}
                {exports.data.length === 0 ? (
                  <tr>
                    <td className="p-6 text-center text-neutral-600" colSpan={7}>
                      {t("exports.empty")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
      <Card title={t("exports.new")}>
        {paused ? (
          <Notice tone="warning" className="mb-3">
            {t("exports.paused")}
          </Notice>
        ) : null}
        <form onSubmit={submit}>
          <Field id="x-format" label={t("common.format")}>
            <Select id="x-format" value={form.format} onChange={(e) => setForm({ ...form, format: e.target.value, template_id: "" })}>
              {EXPORT_FORMATS.map((f) => (
                <option key={f} value={f}>
                  {t(`exports.format.${f}` as "exports.format.csv")}
                </option>
              ))}
            </Select>
          </Field>
          {usable.length ? (
            <Field id="x-template" label={t("exports.template")}>
              <Select id="x-template" value={form.template_id} onChange={(e) => setForm({ ...form, template_id: e.target.value })}>
                <option value="">{t("exports.template.none")}</option>
                {usable.map((tp) => (
                  <option key={tp.id} value={tp.id}>
                    {tp.name}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          <Field id="x-status" label={t("exports.filter.status")}>
            <Select id="x-status" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
              <option value="">{t("exports.filter.all")}</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {t(`status.${s}` as "status.approved")}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="x-client" label={t("exports.filter.client")}>
            <Select id="x-client" value={form.client_company_id} onChange={(e) => setForm({ ...form, client_company_id: e.target.value, template_id: "" })}>
              <option value="">{t("inbox.filter.all")}</option>
              {(clients.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="x-from" label={t("exports.filter.date_from")}>
            <Input id="x-from" type="date" value={form.date_from} onChange={(e) => setForm({ ...form, date_from: e.target.value })} />
          </Field>
          <Field id="x-to" label={t("exports.filter.date_to")}>
            <Input id="x-to" type="date" value={form.date_to} onChange={(e) => setForm({ ...form, date_to: e.target.value })} />
          </Field>
          {form.format === "datev" ? (
            <>
              <Field id="x-dc" label={t("exports.datev.consultant")}>
                <Input id="x-dc" inputMode="numeric" value={form.datev_consultant_number} onChange={(e) => setForm({ ...form, datev_consultant_number: e.target.value })} />
              </Field>
              <Field id="x-dm" label={t("exports.datev.client")}>
                <Input id="x-dm" inputMode="numeric" value={form.datev_client_number} onChange={(e) => setForm({ ...form, datev_client_number: e.target.value })} />
              </Field>
            </>
          ) : null}
          <Button type="submit" disabled={busy || paused}>
            {t("exports.new")}
          </Button>
        </form>
      </Card>
    </div>
  );
}
