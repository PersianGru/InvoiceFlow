"use client";
// The editable half of the review screen: fields, suggestions, lines, checks, actions, shortcuts.
// Mounted with a key per extraction version so the draft always starts from the loaded invoice.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ReviewFields, setPath } from "./review-fields";
import { Badge, Button, Card, Notice, cx } from "./ui";
import type { Invoice } from "@/lib/api";
import { ApiError, post } from "@/lib/api";
import { useApp } from "@/lib/app-context";

export function ReviewPanel({ documentId, invoice, onFocusField, focused, onChanged, canEdit, canApprove }: { documentId: string; invoice: Invoice; onFocusField: (p: string | null) => void; focused: string | null; onChanged: () => Promise<void>; canEdit: boolean; canApprove: boolean }) {
  const { t, refreshLicense } = useApp();
  const router = useRouter();
  const [draft, setDraft] = useState<Record<string, unknown>>(invoice as unknown as Record<string, unknown>);
  const [corrections, setCorrections] = useState<Record<string, unknown>>({});
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const onChange = useCallback((path: string, value: string) => {
    setDraft((d) => setPath(d, path, value === "" ? null : value));
    setCorrections((c) => ({ ...c, [path]: value === "" ? null : value }));
  }, []);

  const act = useCallback(
    async (action: "approve" | "reject" | "needs_info" | "save") => {
      if (!canEdit || busy) return;
      setBusy(true);
      setFlash(null);
      try {
        await post(`/api/v1/documents/${documentId}/review`, { action, corrections });
        setFlash({ tone: "success", text: t(action === "approve" ? "review.approved" : action === "reject" ? "review.rejected" : "review.saved") });
        await onChanged();
      } catch (err) {
        setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
        if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
      } finally {
        setBusy(false);
      }
    },
    [busy, canEdit, corrections, documentId, onChanged, refreshLicense, t],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const typing = target !== null && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT");
      if (e.key === "Escape") {
        (document.activeElement as HTMLElement | null)?.blur();
        if (!typing) router.push("/inbox");
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "a" && canApprove) void act("approve");
      if (e.key === "r") void act("reject");
      if (e.key === "n") document.getElementById("note")?.focus();
      if (e.key === "s") void act("save");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [act, canApprove, router]);

  const dirty = Object.keys(corrections).length > 0;
  return (
    <div className="space-y-4">
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"}>
          {flash.text}
        </Notice>
      ) : null}
      {canEdit ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" disabled={busy || !dirty} onClick={() => act("save")}>
            {t("review.save")}
          </Button>
          <Button size="sm" variant="danger" disabled={busy} onClick={() => act("reject")}>
            {t("review.reject")}
          </Button>
          {canApprove ? (
            <Button size="sm" disabled={busy} onClick={() => act("approve")}>
              {t("review.approve")}
            </Button>
          ) : null}
        </div>
      ) : null}
      <Card title={t("review.fields")}>
        <ReviewFields invoice={invoice} draft={draft} corrections={corrections} onChange={onChange} onFocusField={onFocusField} focused={focused} t={t} readOnly={!canEdit} />
        {invoice.suggested_gl_account || invoice.suggested_cost_category || invoice.suggested_vat_code || invoice.direction ? (
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {invoice.direction ? <Badge tone="info">{t(`review.direction.${invoice.direction}` as "review.direction.purchase")}</Badge> : null}
            {invoice.suggested_gl_account ? (
              <Badge>
                {t("review.gl_account")}: {invoice.suggested_gl_account}
              </Badge>
            ) : null}
            {invoice.suggested_cost_category ? (
              <Badge>
                {t("review.cost_category")}: {invoice.suggested_cost_category}
              </Badge>
            ) : null}
            {invoice.suggested_vat_code ? (
              <Badge>
                {t("review.vat_code")}: {invoice.suggested_vat_code}
              </Badge>
            ) : null}
          </div>
        ) : null}
      </Card>
      {invoice.lines.length ? (
        <Card title={t("review.lines")}>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-neutral-600">
              <tr>
                <th className="p-1">{t("field.line.description")}</th>
                <th className="p-1 text-right">{t("field.line.quantity")}</th>
                <th className="p-1 text-right">{t("field.line.unit_price")}</th>
                <th className="p-1 text-right">{t("field.line.net_amount")}</th>
                <th className="p-1 text-right">{t("field.line.vat_rate")}</th>
              </tr>
            </thead>
            <tbody>
              {invoice.lines.map((ln, i) => (
                <tr key={i} className="border-t border-neutral-100 dark:border-neutral-800">
                  <td className="p-1">{String(ln.description ?? "")}</td>
                  <td className="p-1 text-right tabular-nums">{String(ln.quantity ?? "")}</td>
                  <td className="p-1 text-right tabular-nums">{String(ln.unit_price ?? "")}</td>
                  <td className="p-1 text-right tabular-nums">{String(ln.net_amount ?? "")}</td>
                  <td className="p-1 text-right tabular-nums">{String(ln.vat_rate ?? "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
      <Card title={t("review.validation")}>
        <ul className="space-y-1 text-sm">
          {invoice.validation.map((v, i) => (
            <li key={i} className="flex items-start gap-2">
              <Badge tone={v.level === "error" ? "danger" : v.level === "warning" ? "warning" : "success"} className={cx("mt-0.5 shrink-0")}>
                {v.level}
              </Badge>
              <span>
                {v.reason}
                {v.field ? <span className="text-neutral-500"> ({v.field})</span> : null}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
