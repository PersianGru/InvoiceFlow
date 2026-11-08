"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";

import { Badge, Button, Field, Input, Notice, Select, Spinner, confidenceTone, statusTone } from "@/components/ui";
import { formatDate, formatDateTime, formatMoney } from "@/i18n";
import { ApiError, get, imageUrl, post, upload, type ClientCompany, type DocumentPage, type ExportRow, type User } from "@/lib/api";
import { EXPORT_FORMATS } from "@/lib/integrations";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

const STATUSES = ["received", "processing", "needs_review", "needs_info", "approved", "rejected", "quarantined", "failed"] as const;
const PAGE = 50;

export default function InboxPage() {
  const { t, locale, user, license, refreshLicense } = useApp();
  const router = useRouter();
  const [filters, setFilters] = useState({ status: "", client_company_id: "", channel: "", supplier: "", date_from: "", date_to: "", amount_min: "", amount_max: "", q: "" });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [flash, setFlash] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const isStaff = user?.role === "firm_owner" || user?.role === "accountant" || user?.role === "junior_accountant";
  const canApprove = user?.role === "firm_owner" || user?.role === "accountant";

  const docs = useFetch<DocumentPage>(() => get<DocumentPage>("/api/v1/documents", { ...filters, offset, limit: PAGE }), [JSON.stringify(filters), offset]);
  const clients = useFetch<ClientCompany[]>(() => (isStaff ? get<ClientCompany[]>("/api/v1/client-companies") : Promise.resolve([])), [isStaff]);
  const users = useFetch<User[]>(() => (user?.role === "firm_owner" ? get<User[]>("/api/v1/users") : Promise.resolve([])), [user?.role]);
  const clientName = useMemo(() => new Map((clients.data ?? []).map((c) => [c.id, c.name])), [clients.data]);
  const intakeOff = license !== null && !license.core_on;

  async function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (!files.length) return;
    setBusy(true);
    setFlash(null);
    let count = 0;
    try {
      for (const f of files) {
        const form = new FormData();
        form.append("file", f);
        const res = await upload<{ batch: unknown[] | null }>(`/api/v1/documents${filters.client_company_id ? `?client_company_id=${filters.client_company_id}` : ""}`, form);
        count += res.batch ? res.batch.length : 1;
      }
      setFlash({ tone: "success", text: t("inbox.uploaded", { n: count }) });
      await docs.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function bulk(action: "approve" | "reject" | "reprocess" | "assign", userId?: string | null) {
    if (!selected.size) return;
    setBusy(true);
    try {
      const res = await post<{ done: string[]; skipped: unknown[] }>("/api/v1/documents/bulk", { ids: [...selected], action, user_id: userId ?? null });
      setFlash({ tone: "success", text: t("inbox.bulk.done", { n: res.done.length, s: res.skipped.length }) });
      setSelected(new Set());
      await docs.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    } finally {
      setBusy(false);
    }
  }

  async function exportSelected(format: string) {
    if (!selected.size) return;
    setBusy(true);
    try {
      const e = await post<ExportRow>("/api/v1/exports", { format, document_ids: [...selected] });
      setFlash({ tone: "success", text: t("exports.created", { name: e.file_name, n: e.document_count }) });
      window.open(imageUrl(e.download_url), "_blank", "noopener");
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
    }
  }

  const rows = docs.data?.items ?? [];
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const setFilter = (k: keyof typeof filters) => (e: { target: { value: string } }) => {
    setOffset(0);
    setFilters((f) => ({ ...f, [k]: e.target.value }));
  };

  function onRowKey(e: KeyboardEvent<HTMLTableRowElement>, id: string) {
    if (e.key === "Enter") router.push(`/documents/${id}`);
    if (e.key === " ") {
      e.preventDefault();
      toggle(id);
    }
  }
  function toggle(id: string) {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">{t("inbox.title")}</h1>
        <div className="ml-auto flex items-center gap-2">
          <input ref={fileRef} type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,.heic,.xml,.zip" className="sr-only" id="upload" onChange={onUpload} disabled={busy || intakeOff} />
          <Button onClick={() => fileRef.current?.click()} disabled={busy || intakeOff} title={intakeOff ? t("intake.paused") : undefined}>
            {busy ? t("inbox.uploading") : t("inbox.upload")}
          </Button>
        </div>
      </div>
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      <details className="mb-3 rounded-md border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900" open>
        <summary className="cursor-pointer text-sm font-medium">{t("common.filters")}</summary>
        <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field id="f-q" label={t("common.search")}>
            <Input id="f-q" value={filters.q} onChange={setFilter("q")} />
          </Field>
          <Field id="f-status" label={t("inbox.filter.status")}>
            <Select id="f-status" value={filters.status} onChange={setFilter("status")}>
              <option value="">{t("inbox.filter.all")}</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {t(`status.${s}`)}
                </option>
              ))}
            </Select>
          </Field>
          {isStaff ? (
            <Field id="f-client" label={t("inbox.filter.client")}>
              <Select id="f-client" value={filters.client_company_id} onChange={setFilter("client_company_id")}>
                <option value="">{t("inbox.filter.all")}</option>
                {(clients.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          <Field id="f-channel" label={t("inbox.filter.channel")}>
            <Select id="f-channel" value={filters.channel} onChange={setFilter("channel")}>
              <option value="">{t("inbox.filter.all")}</option>
              {["upload", "email", "whatsapp", "telegram", "api"].map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="f-supplier" label={t("inbox.filter.supplier")}>
            <Input id="f-supplier" value={filters.supplier} onChange={setFilter("supplier")} />
          </Field>
          <Field id="f-from" label={t("inbox.filter.date_from")}>
            <Input id="f-from" type="date" value={filters.date_from} onChange={setFilter("date_from")} />
          </Field>
          <Field id="f-to" label={t("inbox.filter.date_to")}>
            <Input id="f-to" type="date" value={filters.date_to} onChange={setFilter("date_to")} />
          </Field>
          <Field id="f-min" label={t("inbox.filter.amount_min")}>
            <Input id="f-min" type="number" step="0.01" value={filters.amount_min} onChange={setFilter("amount_min")} />
          </Field>
          <Field id="f-max" label={t("inbox.filter.amount_max")}>
            <Input id="f-max" type="number" step="0.01" value={filters.amount_max} onChange={setFilter("amount_max")} />
          </Field>
        </div>
      </details>
      {isStaff && selected.size > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md bg-neutral-100 p-2 text-sm dark:bg-neutral-800" role="toolbar" aria-label={t("common.actions")}>
          <span>{t("inbox.bulk.selected", { n: selected.size })}</span>
          {canApprove ? (
            <Button size="sm" onClick={() => bulk("approve")} disabled={busy || intakeOff}>
              {t("inbox.bulk.approve")}
            </Button>
          ) : null}
          <Button size="sm" variant="danger" onClick={() => bulk("reject")} disabled={busy || intakeOff}>
            {t("inbox.bulk.reject")}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => bulk("reprocess")} disabled={busy || intakeOff}>
            {t("inbox.bulk.reprocess")}
          </Button>
          <Select
            aria-label={t("inbox.bulk.export")}
            className="w-auto py-1"
            value=""
            disabled={busy || intakeOff}
            onChange={(e) => {
              if (e.target.value) void exportSelected(e.target.value);
            }}
          >
            <option value="">{t("inbox.bulk.export")}</option>
            {EXPORT_FORMATS.map((f) => (
              <option key={f} value={f}>
                {t(`exports.format.${f}` as "exports.format.csv")}
              </option>
            ))}
          </Select>
          {users.data && users.data.length ? (
            <Select aria-label={t("inbox.bulk.assign")} className="w-auto py-1" defaultValue="" onChange={(e) => bulk("assign", e.target.value || null)}>
              <option value="">{t("inbox.bulk.assign")}…</option>
              {users.data.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.display_name || u.email}
                </option>
              ))}
            </Select>
          ) : null}
        </div>
      ) : null}
      {docs.loading && !docs.data ? <Spinner label={t("common.loading")} /> : null}
      {docs.error ? (
        <Notice tone="danger" role="alert">
          {docs.error}
        </Notice>
      ) : null}
      {docs.data ? (
        <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              <tr>
                {isStaff ? (
                  <th className="p-2">
                    <input type="checkbox" aria-label="Select all" checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)))} />
                  </th>
                ) : null}
                <th className="p-2">{t("inbox.col.received")}</th>
                <th className="p-2">{t("inbox.col.supplier")}</th>
                <th className="p-2">{t("inbox.col.number")}</th>
                <th className="p-2">{t("inbox.col.date")}</th>
                <th className="p-2 text-right">{t("inbox.col.amount")}</th>
                <th className="p-2">{t("inbox.col.status")}</th>
                <th className="p-2">{t("inbox.col.confidence")}</th>
                <th className="p-2">{t("inbox.col.channel")}</th>
                {isStaff ? <th className="p-2">{t("inbox.filter.client")}</th> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id} tabIndex={0} onKeyDown={(e) => onRowKey(e, d.id)} className="border-t border-neutral-100 hover:bg-neutral-50 focus:bg-blue-50 dark:border-neutral-800 dark:hover:bg-neutral-800 dark:focus:bg-neutral-800">
                  {isStaff ? (
                    <td className="p-2">
                      <input type="checkbox" aria-label={d.original_filename} checked={selected.has(d.id)} onChange={() => toggle(d.id)} />
                    </td>
                  ) : null}
                  <td className="whitespace-nowrap p-2">{formatDateTime(locale, d.received_at)}</td>
                  <td className="p-2">
                    <Link href={`/documents/${d.id}`} className="font-medium underline-offset-2 hover:underline">
                      {d.supplier_name ?? d.original_filename}
                    </Link>
                  </td>
                  <td className="p-2">{d.invoice_number ?? t("common.none")}</td>
                  <td className="whitespace-nowrap p-2">{formatDate(locale, d.issue_date)}</td>
                  <td className="whitespace-nowrap p-2 text-right tabular-nums">
                    {formatMoney(locale, d.gross_total, d.currency)}
                    {d.payment_status === "paid" ? <Badge tone="success" className="ml-1">{t("payment.paid")}</Badge> : d.payment_status === "partial" ? <Badge tone="warning" className="ml-1">{t("payment.partial")}</Badge> : null}
                  </td>
                  <td className="p-2">
                    <Badge tone={statusTone(d.status)}>{t(`status.${d.status}` as "status.received")}</Badge>
                  </td>
                  <td className="p-2">{d.min_confidence !== null ? <Badge tone={confidenceTone(d.min_confidence)}>{Math.round(d.min_confidence * 100)}%</Badge> : d.structured_format ? <Badge tone="success">{d.structured_format}</Badge> : null}</td>
                  <td className="p-2">{d.channel}</td>
                  {isStaff ? <td className="p-2">{d.client_company_id ? (clientName.get(d.client_company_id) ?? "…") : t("common.none")}</td> : null}
                </tr>
              ))}
              {rows.length === 0 ? (
                <tr>
                  <td className="p-6 text-center text-neutral-600" colSpan={10}>
                    {t("inbox.empty")}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
          <div className="flex items-center justify-between border-t border-neutral-100 p-2 text-sm dark:border-neutral-800">
            <span>
              {docs.data.total} {t("common.total").toLowerCase()}
            </span>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                {t("common.previous")}
              </Button>
              <Button size="sm" variant="secondary" disabled={offset + PAGE >= docs.data.total} onClick={() => setOffset(offset + PAGE)}>
                {t("common.next")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
