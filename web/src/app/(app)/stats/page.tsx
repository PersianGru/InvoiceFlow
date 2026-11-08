"use client";
import { useState } from "react";

import { Card, Select, Spinner } from "@/components/ui";
import { formatPercent } from "@/i18n";
import { get, type Stats } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

function Bar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-28 shrink-0 truncate">{label}</span>
      <div className="h-3 flex-1 rounded bg-neutral-200 dark:bg-neutral-800" role="img" aria-label={`${label}: ${value}`}>
        <div className="h-3 rounded bg-blue-600" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-10 text-right tabular-nums">{value}</span>
    </div>
  );
}

export default function StatsPage() {
  const { t, locale } = useApp();
  const [days, setDays] = useState(30);
  const stats = useFetch<Stats>(() => get<Stats>("/api/v1/stats", { days }), [days]);
  const s = stats.data;
  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <h1 className="text-xl font-semibold">{t("stats.title")}</h1>
        <label className="sr-only" htmlFor="days">
          {t("stats.period", { days })}
        </label>
        <Select id="days" className="ml-auto w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 30, 90, 365].map((d) => (
            <option key={d} value={d}>
              {t("stats.period", { days: d })}
            </option>
          ))}
        </Select>
      </div>
      {!s ? <Spinner label={t("common.loading")} /> : null}
      {s ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Card title={t("stats.volume")}>
            <p className="text-3xl font-semibold tabular-nums">{s.documents.total}</p>
          </Card>
          <Card title={t("stats.auto_approval")}>
            <p className="text-3xl font-semibold tabular-nums">{formatPercent(locale, s.auto_approval_rate)}</p>
          </Card>
          <Card title={t("stats.structured")}>
            <p className="text-3xl font-semibold tabular-nums">{s.documents.structured}</p>
          </Card>
          <Card title={t("stats.processing")}>
            <p className="text-3xl font-semibold tabular-nums">
              {s.processing_ms.avg ?? "—"} <span className="text-base text-neutral-500">/ {s.processing_ms.p95 ?? "—"} ms</span>
            </p>
          </Card>
          <Card title={t("stats.by_status")} className="md:col-span-2">
            {Object.entries(s.documents.by_status).map(([k, v]) => (
              <Bar key={k} label={t(`status.${k}` as "status.received")} value={v} max={Math.max(...Object.values(s.documents.by_status), 1)} />
            ))}
          </Card>
          <Card title={t("stats.by_channel")} className="md:col-span-2">
            {Object.entries(s.documents.by_channel).map(([k, v]) => (
              <Bar key={k} label={k} value={v} max={Math.max(...Object.values(s.documents.by_channel), 1)} />
            ))}
          </Card>
          <Card title={t("stats.by_day")} className="md:col-span-2 xl:col-span-4">
            <div className="flex h-32 items-end gap-0.5" role="img" aria-label={t("stats.by_day")}>
              {s.documents.by_day.map((d) => {
                const max = Math.max(...s.documents.by_day.map((x) => x.count), 1);
                return <div key={d.day ?? "x"} title={`${d.day}: ${d.count}`} className="flex-1 rounded-t bg-blue-600" style={{ height: `${Math.max(4, (d.count / max) * 100)}%` }} />;
              })}
            </div>
          </Card>
          <Card title={t("stats.top_suppliers")} className="md:col-span-2">
            <table className="w-full text-sm">
              <tbody>
                {s.suppliers.map((sp) => (
                  <tr key={sp.id} className="border-t border-neutral-100 dark:border-neutral-800">
                    <td className="p-1">{sp.name}</td>
                    <td className="p-1 text-right tabular-nums">{sp.documents}</td>
                    <td className="p-1 text-right tabular-nums">{formatPercent(locale, sp.accuracy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card title={t("stats.ai")} className="md:col-span-2">
            <table className="w-full text-sm">
              <tbody>
                {s.ai_calls.length === 0 ? (
                  <tr>
                    <td className="p-1 text-neutral-500">{t("common.none")}</td>
                  </tr>
                ) : null}
                {s.ai_calls.map((a) => (
                  <tr key={a.provider} className="border-t border-neutral-100 dark:border-neutral-800">
                    <td className="p-1">{a.provider}</td>
                    <td className="p-1 text-right tabular-nums">{a.calls}</td>
                    <td className="p-1 text-right tabular-nums">
                      {a.tokens_in} / {a.tokens_out}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
