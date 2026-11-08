"use client";
// The editable field list of the review screen: value, confidence, provenance, location highlight.
import { useCallback, useMemo, type KeyboardEvent } from "react";

import type { FieldMeta, Invoice } from "@/lib/api";
import { Badge, Input, confidenceTone, cx } from "./ui";
import type { MessageKey } from "@/i18n";

export const HEADER_FIELDS: Array<{ path: string; key: MessageKey; type?: "date" | "text" | "number" }> = [
  { path: "document_type", key: "field.document_type" },
  { path: "number", key: "field.number" },
  { path: "issue_date", key: "field.issue_date", type: "date" },
  { path: "delivery_date", key: "field.delivery_date", type: "date" },
  { path: "currency", key: "field.currency" },
  { path: "supplier.name", key: "field.supplier.name" },
  { path: "supplier.vat_id", key: "field.supplier.vat_id" },
  { path: "supplier.iban", key: "field.supplier.iban" },
  { path: "supplier.bic", key: "field.supplier.bic" },
  { path: "supplier.email", key: "field.supplier.email" },
  { path: "buyer.name", key: "field.buyer.name" },
  { path: "buyer.vat_id", key: "field.buyer.vat_id" },
  { path: "totals.net_total", key: "field.totals.net_total", type: "number" },
  { path: "totals.vat_total", key: "field.totals.vat_total", type: "number" },
  { path: "totals.gross_total", key: "field.totals.gross_total", type: "number" },
  { path: "payment.due_date", key: "field.payment.due_date", type: "date" },
  { path: "payment.payment_reference", key: "field.payment.payment_reference" },
  { path: "references.purchase_order", key: "field.references.purchase_order" },
];

export function getPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj;
  for (const p of path.split(".")) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}

export function setPath(obj: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = { ...obj };
  const parts = path.split(".");
  let cur: Record<string, unknown> = out;
  for (const p of parts.slice(0, -1)) {
    const next = cur[p];
    const copy: Record<string, unknown> = next && typeof next === "object" ? { ...(next as Record<string, unknown>) } : {};
    cur[p] = copy;
    cur = copy;
  }
  cur[parts[parts.length - 1]] = value;
  return out;
}

export function provenanceLabel(meta: FieldMeta | undefined, t: (k: MessageKey, v?: Record<string, string | number>) => string): string {
  if (!meta) return "";
  if (meta.source === "structured") return t("review.structured");
  if (meta.source === "human" || !meta.ai_generated) return t("review.confirmed");
  return `${t("review.ai_generated")} · ${t("review.confidence", { pct: Math.round(meta.confidence * 100) })}`;
}

export function ReviewFields({ invoice, draft, corrections, onChange, onFocusField, focused, t, readOnly }: { invoice: Invoice; draft: Record<string, unknown>; corrections: Record<string, unknown>; onChange: (path: string, value: string) => void; onFocusField: (path: string | null) => void; focused: string | null; t: (k: MessageKey, v?: Record<string, string | number>) => string; readOnly: boolean }) {
  const order = useMemo(() => HEADER_FIELDS.map((f) => f.path), []);
  const move = useCallback(
    (e: KeyboardEvent<HTMLInputElement>, path: string) => {
      if (e.key !== "j" && e.key !== "k") return;
      if ((e.target as HTMLInputElement).value && e.key.length === 1 && !e.altKey) return; // typing in a filled field
      e.preventDefault();
      const i = order.indexOf(path);
      const next = order[e.key === "j" ? Math.min(order.length - 1, i + 1) : Math.max(0, i - 1)];
      document.getElementById(`field-${next}`)?.focus();
    },
    [order],
  );
  return (
    <dl className="divide-y divide-neutral-100 dark:divide-neutral-800">
      {HEADER_FIELDS.map((f) => {
        const meta = invoice.meta[f.path];
        const value = getPath(draft, f.path);
        const changed = f.path in corrections;
        return (
          <div key={f.path} className={cx("grid grid-cols-5 items-center gap-2 py-1.5", focused === f.path && "bg-blue-50 dark:bg-blue-950/30")}>
            <dt className="col-span-2 text-sm text-neutral-700 dark:text-neutral-300">
              <label htmlFor={`field-${f.path}`}>{t(f.key)}</label>
              {meta ? (
                <div className="mt-0.5 flex items-center gap-1">
                  <Badge tone={meta.source === "structured" || !meta.ai_generated ? "success" : confidenceTone(meta.confidence)} className="cursor-help" title={provenanceLabel(meta, t)}>
                    {meta.source === "structured" ? "XML" : !meta.ai_generated ? "✓" : `${Math.round(meta.confidence * 100)}%`}
                  </Badge>
                  {meta.ai_generated && meta.source !== "structured" ? <span className="sr-only">{t("review.ai_generated")}</span> : null}
                </div>
              ) : null}
            </dt>
            <dd className="col-span-3">
              <Input id={`field-${f.path}`} type={f.type === "date" ? "date" : "text"} inputMode={f.type === "number" ? "decimal" : undefined} value={value === undefined || value === null ? "" : String(value)} onChange={(e) => onChange(f.path, e.target.value)} onFocus={() => onFocusField(f.path)} onBlur={() => onFocusField(null)} onKeyDown={(e) => move(e, f.path)} readOnly={readOnly} aria-describedby={meta ? `prov-${f.path}` : undefined} className={cx(changed && "border-blue-600 ring-2 ring-blue-600/30")} />
              {meta ? (
                <span id={`prov-${f.path}`} className="sr-only">
                  {provenanceLabel(meta, t)}
                </span>
              ) : null}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
