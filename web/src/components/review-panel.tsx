"use client";
// The editable half of the review screen: fields, suggestions, lines, checks, actions, shortcuts.
// Mounted with a key per extraction version so the draft always starts from the loaded invoice.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AllowancesEditor, type Line, LinesEditor } from "./lines-editor";
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

  const lines = (Array.isArray(draft.lines) ? draft.lines : []) as Line[];
  const allowances = (Array.isArray(draft.allowances_charges) ? draft.allowances_charges : []) as Line[];
  // a table is corrected as a whole; per-row provenance is dropped (the server marks the list as confirmed)
  const onRows = (key: "lines" | "allowances_charges") => (next: Line[]) => {
    const clean = next.map((ln) => Object.fromEntries(Object.entries(ln).filter(([k, v]) => k !== "meta" && v !== null && v !== undefined)));
    setDraft((d) => ({ ...d, [key]: clean }));
    setCorrections((c) => ({ ...c, [key]: clean }));
  };
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
      {lines.length || canEdit ? (
        <Card title={t("review.lines")}>
          <LinesEditor lines={lines} onChange={onRows("lines")} readOnly={!canEdit} t={t} />
        </Card>
      ) : null}
      {allowances.length || canEdit ? (
        <Card title={t("review.allowances")}>
          <AllowancesEditor rows={allowances} onChange={onRows("allowances_charges")} readOnly={!canEdit} t={t} />
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
