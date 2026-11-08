"use client";
import { useState } from "react";

import { Input, Spinner } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { get, type AuditRow } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

export default function AuditPage() {
  const { t, locale } = useApp();
  const [action, setAction] = useState("");
  const rows = useFetch<AuditRow[]>(() => get<AuditRow[]>("/api/v1/audit", { action, limit: 200 }), [action]);
  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <h1 className="text-xl font-semibold">{t("audit.title")}</h1>
        <label className="sr-only" htmlFor="a-action">
          {t("audit.action")}
        </label>
        <Input id="a-action" className="ml-auto w-64" placeholder="document.upload" value={action} onChange={(e) => setAction(e.target.value)} />
      </div>
      {rows.loading && !rows.data ? <Spinner label={t("common.loading")} /> : null}
      {rows.data ? (
        <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              <tr>
                <th className="p-2">{t("audit.when")}</th>
                <th className="p-2">{t("audit.who")}</th>
                <th className="p-2">{t("audit.action")}</th>
                <th className="p-2">{t("audit.target")}</th>
                <th className="p-2">{t("audit.result")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.data.map((r) => (
                <tr key={r.id} className="border-t border-neutral-100 align-top dark:border-neutral-800">
                  <td className="whitespace-nowrap p-2">{formatDateTime(locale, r.created_at)}</td>
                  <td className="p-2 text-xs">{r.actor_role ?? "system"}</td>
                  <td className="p-2 font-mono text-xs">{r.action}</td>
                  <td className="p-2 font-mono text-xs">
                    {r.target_type} {r.target_id ? r.target_id.slice(0, 8) : ""}
                    {r.after ? <details className="mt-1"><summary className="cursor-pointer text-neutral-500">details</summary><pre className="max-w-md overflow-x-auto whitespace-pre-wrap text-[10px]">{JSON.stringify({ before: r.before, after: r.after }, null, 1)}</pre></details> : null}
                  </td>
                  <td className="p-2">{r.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
