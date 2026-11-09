"use client";
// Trash: documents moved to the trash, restore, delete for good (owner, after 24 h), and
// bulk requests waiting for a firm owner's approval. Every action asks for a justification.
import { useState } from "react";

import { useReason } from "@/components/reason-dialog";
import { Badge, Button, Card, Notice, Spinner } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ApiError, get, post } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

interface TrashRow {
  id: string;
  original_filename: string;
  channel: string;
  sender_identity: string;
  received_at: string | null;
  deleted_at: string | null;
  delete_reason: string | null;
  purge_after: string | null;
  purged_at: string | null;
  sha256: string;
}

interface TrashRequestRow {
  id: string;
  reason: string;
  count: number;
  status: string;
  filter: Record<string, string> | null;
  decision_note: string | null;
  self_approved: boolean;
  executed_count: number;
  created_at: string | null;
  expires_at: string;
}

export default function TrashPage() {
  const { t, locale, user } = useApp();
  const isOwner = user?.role === "firm_owner" || user?.role === "super_admin";
  const rows = useFetch<TrashRow[]>(() => get<TrashRow[]>("/api/v1/trash"), []);
  const reqs = useFetch<TrashRequestRow[]>(() => get<TrashRequestRow[]>("/api/v1/trash-requests"), []);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [dialog, ask] = useReason();

  async function act(title: string, path: string, field: "reason" | "note", danger = false) {
    const text = await ask(title, { danger });
    if (!text) return;
    try {
      await post(path, { [field]: text });
      setFlash({ tone: "success", text: t("common.saved") });
      await rows.reload();
      await reqs.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    }
  }

  const pending = (reqs.data ?? []).filter((r) => r.status === "pending");
  return (
    <div className="space-y-4">
      {dialog}
      <h1 className="text-xl font-semibold">{t("nav.trash")}</h1>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">{t("trash.intro")}</p>
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"}>
          {flash.text}
        </Notice>
      ) : null}
      {pending.length ? (
        <Card title={t("trash.pending")}>
          <ul className="space-y-2 text-sm">
            {pending.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2">
                <Badge tone="warning">{t("trash.docs", { n: r.count })}</Badge>
                <span>“{r.reason}”</span>
                <span className="text-xs text-neutral-600">{t("trash.expires", { date: formatDateTime(locale, r.expires_at) })}</span>
                {isOwner ? (
                  <>
                    <Button size="sm" variant="danger" onClick={() => void act(t("trash.approve"), `/api/v1/trash-requests/${r.id}/approve`, "note", true)}>
                      {t("trash.approve")}
                    </Button>
                    <Button size="sm" variant="secondary" onClick={() => void act(t("trash.reject"), `/api/v1/trash-requests/${r.id}/reject`, "note")}>
                      {t("trash.reject")}
                    </Button>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
      {!rows.data ? (
        <Spinner label={t("common.loading")} />
      ) : (
        <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              <tr>
                <th className="p-2">{t("trash.file")}</th>
                <th className="p-2">{t("trash.reason")}</th>
                <th className="p-2">{t("trash.deleted_at")}</th>
                <th className="p-2">{t("trash.purge_after")}</th>
                <th className="p-2">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.data.map((d) => (
                <tr key={d.id} className="border-t border-neutral-100 align-top dark:border-neutral-800">
                  <td className="p-2">
                    <div className="font-medium">{d.original_filename}</div>
                    <div className="text-xs text-neutral-600">
                      {d.channel} · {d.sender_identity}
                    </div>
                  </td>
                  <td className="p-2">{d.delete_reason}</td>
                  <td className="whitespace-nowrap p-2">{formatDateTime(locale, d.deleted_at)}</td>
                  <td className="whitespace-nowrap p-2">{d.purged_at ? <Badge>{t("trash.purged")}</Badge> : formatDateTime(locale, d.purge_after)}</td>
                  <td className="p-2">
                    {!d.purged_at ? (
                      <div className="flex flex-wrap gap-1">
                        <Button size="sm" variant="secondary" onClick={() => void act(t("trash.restore"), `/api/v1/trash/${d.id}/restore`, "reason")}>
                          {t("trash.restore")}
                        </Button>
                        {isOwner ? (
                          <Button size="sm" variant="danger" onClick={() => void act(t("trash.purge"), `/api/v1/trash/${d.id}/purge`, "reason", true)}>
                            {t("trash.purge")}
                          </Button>
                        ) : null}
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
              {rows.data.length === 0 ? (
                <tr>
                  <td className="p-6 text-center text-neutral-600" colSpan={5}>
                    {t("trash.empty")}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
