"use client";
// Side-by-side review: original pages with highlighted field locations | editable fields, checks,
// lines (ReviewPanel), comments, timeline. Keyboard: A approve, R reject, N note, S save, Esc back.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";

import { QuarantineActions } from "@/components/quarantine-actions";
import { ReviewPanel } from "@/components/review-panel";
import { Badge, Button, Card, Notice, Select, Spinner, Textarea, cx, statusTone } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ApiError, API_URL, get, post, type Comment, type DocumentRow, type InvoiceEnvelope, type TimelineEvent } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { EXPORT_FORMATS } from "@/lib/integrations";
import { useFetch } from "@/lib/use-fetch";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const { t, locale, user, license } = useApp();
  const doc = useFetch<DocumentRow>(() => get<DocumentRow>(`/api/v1/documents/${id}`), [id]);
  const env = useFetch<InvoiceEnvelope | null>(() => get<InvoiceEnvelope>(`/api/v1/documents/${id}/invoice`).catch((e) => (e instanceof ApiError && e.status === 404 ? null : Promise.reject(e))), [id]);
  const timeline = useFetch<TimelineEvent[]>(() => get<TimelineEvent[]>(`/api/v1/documents/${id}/timeline`), [id]);
  const comments = useFetch<Comment[]>(() => get<Comment[]>(`/api/v1/documents/${id}/comments`), [id]);
  const [focused, setFocused] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [note, setNote] = useState("");
  const [noteKind, setNoteKind] = useState<"note" | "question">("note");
  const [busy, setBusy] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  const isStaff = user?.role === "firm_owner" || user?.role === "accountant" || user?.role === "junior_accountant";
  const canApprove = user?.role === "firm_owner" || user?.role === "accountant";
  const licensed = license === null || license.core_on;
  const invoice = env.data?.invoice ?? null;
  const pageCount = doc.data?.page_count ?? 1;
  const hasPreview = doc.data ? doc.data.mime_type === "application/pdf" || doc.data.mime_type.startsWith("image/") : false;
  const highlights = invoice
    ? Object.entries(invoice.meta)
        .filter(([, m]) => m.location && m.location.page === page)
        .map(([path, m]) => ({ path, loc: m.location! }))
    : [];

  const reloadAll = useCallback(async () => {
    await Promise.all([doc.reload(), env.reload(), timeline.reload()]);
  }, [doc, env, timeline]);

  async function sendComment() {
    if (!note.trim()) return;
    setBusy(true);
    try {
      await post(`/api/v1/documents/${id}/comments`, { body: note, kind: noteKind, visible_to_client: noteKind === "question" });
      setNote("");
      await Promise.all([comments.reload(), doc.reload()]);
    } catch (err) {
      setFlash(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  if (doc.loading && !doc.data) return <Spinner label={t("common.loading")} />;
  if (doc.error || !doc.data)
    return (
      <Notice tone="danger" role="alert">
        {doc.error ?? t("common.error")}
      </Notice>
    );
  const d = doc.data;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Link href="/inbox" className="text-sm underline">
          ← {t("nav.inbox")}
        </Link>
        <h1 className="text-xl font-semibold">{d.supplier_name ?? d.original_filename}</h1>
        <Badge tone={statusTone(d.status)}>{t(`status.${d.status}` as "status.received")}</Badge>
        {d.structured_format ? <Badge tone="success">{d.structured_format}</Badge> : null}
        {d.payment_status === "paid" ? <Badge tone="success">{t("payment.paid_on", { date: d.paid_at ?? "" })}</Badge> : d.payment_status === "partial" ? <Badge tone="warning">{t("payment.partial_amount", { amount: d.paid_amount ?? "" })}</Badge> : null}
        {env.data ? <span className="text-xs text-neutral-500">{t("review.version", { v: env.data.version, provider: env.data.provider })}</span> : null}
        <div className="ml-auto flex flex-wrap gap-2">
          <a className="text-sm underline" href={`${API_URL}/api/v1/documents/${id}/download`}>
            {t("review.download")}
          </a>
          {isStaff ? (
            <Select
              aria-label={t("review.export_as")}
              className="w-auto py-1 text-sm"
              value=""
              disabled={!licensed || !env.data}
              onChange={(e) => {
                if (e.target.value) window.open(`${API_URL}/api/v1/documents/${id}/export?format=${e.target.value}`, "_blank", "noopener");
              }}
            >
              <option value="">{t("review.export_as")}</option>
              {EXPORT_FORMATS.map((f) => (
                <option key={f} value={f}>
                  {t(`exports.format.${f}` as "exports.format.csv")}
                </option>
              ))}
            </Select>
          ) : null}
          {isStaff ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy || !licensed}
              onClick={async () => {
                setBusy(true);
                try {
                  await post(`/api/v1/documents/${id}/reprocess`);
                  await reloadAll();
                } catch (err) {
                  setFlash(err instanceof ApiError ? err.message : t("common.error"));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {t("review.reprocess")}
            </Button>
          ) : null}
        </div>
      </div>
      {flash ? (
        <Notice tone="danger" role="alert" className="mb-3">
          {flash}
        </Notice>
      ) : null}
      {d.status === "quarantined" && d.error === "unknown_sender" && isStaff ? (
        <QuarantineActions documentId={id} senderIdentity={d.sender_identity ?? ""} onDone={reloadAll} />
      ) : d.error ? (
        <Notice tone="warning" className="mb-3">
          {d.error}
        </Notice>
      ) : null}
      <p className="mb-3 text-xs text-neutral-500">{t("review.shortcuts")}</p>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title={hasPreview ? t("review.page", { n: page, total: pageCount }) : t("inbox.col.file")}
          actions={
            hasPreview && pageCount > 1 ? (
              <div className="flex gap-1">
                <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                  {t("common.previous")}
                </Button>
                <Button size="sm" variant="secondary" disabled={page >= pageCount} onClick={() => setPage(page + 1)}>
                  {t("common.next")}
                </Button>
              </div>
            ) : null
          }
        >
          {hasPreview && !imgError ? (
            <div className="relative overflow-hidden rounded border border-neutral-200 dark:border-neutral-800">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`${API_URL}/api/v1/documents/${id}/pages/${page}.png?width=1200`} alt={d.original_filename} className="block w-full" onError={() => setImgError(true)} />
              {highlights.map((h) => (
                <button key={h.path} type="button" aria-label={h.path} title={h.path} onClick={() => document.getElementById(`field-${h.path}`)?.focus()} className={cx("absolute rounded-sm border-2", focused === h.path ? "border-blue-600 bg-blue-500/30" : "border-amber-500/70 bg-amber-300/20 hover:bg-amber-300/40")} style={{ left: `${h.loc.x0 * 100}%`, top: `${h.loc.y0 * 100}%`, width: `${Math.max(0.5, (h.loc.x1 - h.loc.x0) * 100)}%`, height: `${Math.max(0.5, (h.loc.y1 - h.loc.y0) * 100)}%` }} />
              ))}
            </div>
          ) : (
            <p className="text-sm text-neutral-600">{t("review.no_preview")}</p>
          )}
        </Card>
        <div className="space-y-4">
          {invoice && env.data ? (
            <ReviewPanel key={env.data.version} documentId={id} invoice={invoice} onFocusField={setFocused} focused={focused} onChanged={reloadAll} canEdit={Boolean(isStaff && licensed)} canApprove={Boolean(canApprove)} />
          ) : (
            <Card title={t("review.fields")}>
              <p className="text-sm text-neutral-600">{t("review.not_processed")}</p>
            </Card>
          )}
          <Card title={t("review.comments")}>
            <ul className="mb-3 space-y-2 text-sm">
              {(comments.data ?? []).map((c) => (
                <li key={c.id} className={cx("rounded-md p-2", c.kind === "question" ? "bg-amber-50 dark:bg-amber-950/30" : c.kind === "answer" ? "bg-green-50 dark:bg-green-950/30" : "bg-neutral-100 dark:bg-neutral-800")}>
                  <div className="mb-1 flex gap-2 text-xs text-neutral-500">
                    <Badge tone={c.kind === "question" ? "warning" : c.kind === "answer" ? "success" : "neutral"}>{c.kind}</Badge>
                    <span>{formatDateTime(locale, c.created_at)}</span>
                    {c.visible_to_client ? <span>· client</span> : null}
                  </div>
                  <p className="whitespace-pre-wrap">{c.body}</p>
                </li>
              ))}
            </ul>
            <label htmlFor="note" className="sr-only">
              {noteKind === "question" ? t("review.ask_client") : t("review.add_note")}
            </label>
            <Textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder={noteKind === "question" ? t("review.question_placeholder") : t("review.note_placeholder")} />
            <div className="mt-2 flex items-center gap-2">
              {isStaff ? (
                <label className="flex items-center gap-1 text-sm">
                  <input type="checkbox" checked={noteKind === "question"} onChange={(e) => setNoteKind(e.target.checked ? "question" : "note")} /> {t("review.ask_client")}
                </label>
              ) : null}
              <Button size="sm" className="ml-auto" disabled={busy || !note.trim()} onClick={sendComment}>
                {t("review.send")}
              </Button>
            </div>
          </Card>
          <Card title={t("review.timeline")}>
            <ol className="space-y-1 text-xs">
              {(timeline.data ?? []).map((e, i) => (
                <li key={i} className="flex gap-2">
                  <Badge tone={e.status === "error" ? "danger" : e.status === "warning" ? "warning" : e.status === "skipped" ? "neutral" : "success"}>{e.step}</Badge>
                  <span className="text-neutral-600 dark:text-neutral-400">
                    {e.status}
                    {e.duration_ms !== null ? ` · ${e.duration_ms} ms` : ""}
                    {e.detail ? ` · ${Object.entries(e.detail).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join("+") : String(v)}`).join(" ")}` : ""}
                  </span>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>
    </div>
  );
}
