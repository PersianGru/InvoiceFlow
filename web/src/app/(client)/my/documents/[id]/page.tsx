"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Badge, Button, Notice, Spinner, Textarea, statusTone } from "@/components/ui";
import { formatDate, formatDateTime, formatMoney } from "@/i18n";
import { ApiError, API_URL, get, post, type Comment, type DocumentRow } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

const STEPS = ["received", "processing", "needs_info", "approved"] as const;

function stepIndex(status: string): number {
  if (status === "approved") return 3;
  if (status === "needs_info") return 2;
  if (status === "processing" || status === "needs_review") return 1;
  return 0;
}

export default function MyDocumentPage() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useApp();
  const doc = useFetch<DocumentRow>(() => get<DocumentRow>(`/api/v1/documents/${id}`), [id]);
  const comments = useFetch<Comment[]>(() => get<Comment[]>(`/api/v1/documents/${id}/comments`), [id]);
  const [answer, setAnswer] = useState("");
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const d = doc.data;
  const open = (comments.data ?? []).filter((c) => c.kind === "question" && !c.resolved_at);

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!answer.trim()) return;
    try {
      await post(`/api/v1/documents/${id}/comments`, { kind: "answer", body: answer, visible_to_client: true });
      setAnswer("");
      setFlash({ tone: "success", text: t("my.answered") });
      await Promise.all([comments.reload(), doc.reload()]);
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    }
  }

  if (!d) return doc.error ? <Notice tone="danger">{doc.error}</Notice> : <Spinner label={t("common.loading")} />;
  const idx = d.status === "rejected" ? -1 : stepIndex(d.status);
  return (
    <div>
      <Link href="/my" className="text-sm underline">
        ← {t("my.title")}
      </Link>
      <h1 className="mb-1 mt-2 text-xl font-semibold">{d.supplier_name ?? d.original_filename}</h1>
      <p className="mb-3 text-sm text-neutral-600">
        {d.invoice_number ? `${d.invoice_number} · ` : ""}
        {d.issue_date ? formatDate(locale, d.issue_date) : formatDateTime(locale, d.received_at)}
        {d.gross_total ? ` · ${formatMoney(locale, d.gross_total, d.currency)}` : ""}
      </p>
      <ol className="mb-4 grid grid-cols-4 gap-1 text-center text-xs" aria-label={t("my.progress")}>
        {STEPS.map((s, i) => (
          <li key={s} className={`rounded-md p-2 ${i <= idx ? "bg-blue-600 text-white" : "bg-neutral-100 text-neutral-500 dark:bg-neutral-800"}`} aria-current={i === idx ? "step" : undefined}>
            {t(`status.${s}` as "status.received")}
          </li>
        ))}
      </ol>
      <p className="mb-3">
        <Badge tone={statusTone(d.status)}>{t(`status.${d.status}` as "status.received")}</Badge>{" "}
        <a className="ml-2 text-sm underline" href={`${API_URL}/api/v1/documents/${id}/download`}>
          {t("review.download")}
        </a>
      </p>
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      {open.length ? (
        <section className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-700 dark:bg-amber-950">
          <h2 className="mb-2 font-medium">{t("my.question_title")}</h2>
          {open.map((c) => (
            <p key={c.id} className="mb-2 text-sm">
              {c.body}
            </p>
          ))}
          <form onSubmit={send}>
            <Textarea aria-label={t("my.answer")} rows={3} required value={answer} onChange={(e) => setAnswer(e.target.value)} />
            <Button type="submit" className="mt-2">
              {t("review.send")}
            </Button>
          </form>
        </section>
      ) : null}
      {(comments.data ?? []).length ? (
        <section>
          <h2 className="mb-2 text-sm font-medium">{t("review.comments")}</h2>
          <ul className="space-y-2 text-sm">
            {(comments.data ?? []).map((c) => (
              <li key={c.id} className="rounded-md border border-neutral-200 p-2 dark:border-neutral-800">
                <div className="text-xs text-neutral-500">
                  {t(`my.comment.${c.kind}` as "my.comment.question")} · {formatDateTime(locale, c.created_at)}
                </div>
                {c.body}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
