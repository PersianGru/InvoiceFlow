"use client";
import Link from "next/link";
import { useRef, useState, type ChangeEvent } from "react";

import { Badge, Button, Input, Notice, Spinner, statusTone } from "@/components/ui";
import { formatDate, formatDateTime, formatMoney } from "@/i18n";
import { ApiError, get, upload, type DocumentPage, type PortalSummary } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

const STEPS = ["received", "processing", "needs_info", "approved"] as const;

export default function MyDocumentsPage() {
  const { t, locale, intakeOn, refreshLicense } = useApp();
  const [q, setQ] = useState("");
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const docs = useFetch<DocumentPage>(() => get<DocumentPage>("/api/v1/documents", { q, limit: 100 }), [q]);
  const summary = useFetch<PortalSummary>(() => get<PortalSummary>("/api/v1/my/summary"), []);
  const paused = intakeOn === false;

  async function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (!files.length) return;
    setBusy(true);
    setFlash(null);
    let n = 0;
    try {
      for (const f of files) {
        const form = new FormData();
        form.append("file", f);
        const res = await upload<{ batch: unknown[] | null }>("/api/v1/documents", form);
        n += res.batch ? res.batch.length : 1;
      }
      setFlash({ tone: "success", text: t("my.uploaded", { n }) });
      await Promise.all([docs.reload(), summary.reload()]);
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError && err.isLicenseError ? t("intake.paused") : err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const rows = docs.data?.items ?? [];
  return (
    <div>
      <h1 className="mb-3 text-xl font-semibold">{t("my.title")}</h1>
      <div className="mb-3 grid grid-cols-2 gap-2">
        <input ref={fileRef} id="my-upload" type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.heic,.webp,.xml,.zip" className="sr-only" onChange={onUpload} disabled={busy || paused} />
        <Button className="h-14 text-base" onClick={() => fileRef.current?.click()} disabled={busy || paused}>
          {busy ? t("inbox.uploading") : t("my.upload")}
        </Button>
        <Link href="/my/capture" className={paused ? "pointer-events-none opacity-50" : ""} aria-disabled={paused}>
          <Button className="h-14 w-full text-base" variant="secondary" disabled={paused}>
            {t("my.capture")}
          </Button>
        </Link>
      </div>
      {paused ? (
        <Notice tone="warning" className="mb-3">
          {t("intake.paused")}
        </Notice>
      ) : null}
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      {summary.data ? (
        <ol className="mb-3 grid grid-cols-4 gap-1 text-center text-xs" aria-label={t("my.progress")}>
          {STEPS.map((s) => (
            <li key={s} className="rounded-md bg-neutral-100 p-2 dark:bg-neutral-800">
              <div className="text-lg font-semibold tabular-nums">{(summary.data!.by_status[s] ?? 0) + (s === "processing" ? (summary.data!.by_status.needs_review ?? 0) : 0)}</div>
              <div className="text-neutral-600 dark:text-neutral-300">{t(`status.${s}` as "status.received")}</div>
            </li>
          ))}
        </ol>
      ) : null}
      {summary.data && summary.data.open_questions > 0 ? (
        <Notice tone="warning" className="mb-3">
          {t("my.open_questions", { n: summary.data.open_questions })}
        </Notice>
      ) : null}
      <Input aria-label={t("common.search")} placeholder={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} className="mb-3" />
      {docs.loading && !docs.data ? <Spinner label={t("common.loading")} /> : null}
      <ul className="divide-y divide-neutral-100 rounded-md border border-neutral-200 bg-white dark:divide-neutral-800 dark:border-neutral-800 dark:bg-neutral-900">
        {rows.map((d) => (
          <li key={d.id}>
            <Link href={`/my/documents/${d.id}`} className="flex items-center gap-3 p-3 hover:bg-neutral-50 dark:hover:bg-neutral-800">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{d.supplier_name ?? d.original_filename}</div>
                <div className="truncate text-xs text-neutral-500">
                  {d.invoice_number ? `${d.invoice_number} · ` : ""}
                  {d.issue_date ? formatDate(locale, d.issue_date) : formatDateTime(locale, d.received_at)}
                  {d.gross_total ? ` · ${formatMoney(locale, d.gross_total, d.currency)}` : ""}
                </div>
              </div>
              <Badge tone={statusTone(d.status)}>{t(`status.${d.status}` as "status.received")}</Badge>
            </Link>
          </li>
        ))}
        {docs.data && rows.length === 0 ? <li className="p-6 text-center text-sm text-neutral-600">{t("my.empty")}</li> : null}
      </ul>
    </div>
  );
}
