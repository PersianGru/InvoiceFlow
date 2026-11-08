"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Select, Spinner } from "@/components/ui";
import { formatDate } from "@/i18n";
import { ApiError, API_URL, get, post, type ClientCompany, type VidaReport } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

function firstOfLastMonth(): string {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth() - 1, 1).toISOString().slice(0, 10);
}

function endOfLastMonth(): string {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 0).toISOString().slice(0, 10);
}

export default function ReportingPage() {
  const { t, locale, license, refreshLicense } = useApp();
  const reports = useFetch<VidaReport[]>(() => get<VidaReport[]>("/api/v1/reporting/vida"), []);
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const [clientId, setClientId] = useState("");
  const [start, setStart] = useState(firstOfLastMonth);
  const [end, setEnd] = useState(endOfLastMonth);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const licensed = license?.core_on ?? true;
  const clientName = new Map((clients.data ?? []).map((c) => [c.id, c.name]));

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setFlash(null);
    try {
      await fn();
      await reports.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
    }
  }

  async function generate(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await post("/api/v1/reporting/vida", { client_company_id: clientId || null, period_start: start, period_end: end });
    });
  }

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">{t("reporting.title")}</h1>
      <p className="mb-3 text-sm text-neutral-600">{t("reporting.help")}</p>
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Card title={t("reporting.list")}>
            {reports.loading && !reports.data ? <Spinner label={t("common.loading")} /> : null}
            {reports.data && reports.data.length === 0 ? <p className="text-sm text-neutral-600">{t("reporting.empty")}</p> : null}
            {reports.data && reports.data.length > 0 ? (
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-neutral-600">
                  <tr>
                    <th className="p-2">{t("reporting.col.period")}</th>
                    <th className="p-2">{t("reporting.client")}</th>
                    <th className="p-2 text-right">{t("reporting.col.documents")}</th>
                    <th className="p-2">{t("reporting.col.totals")}</th>
                    <th className="p-2">{t("reporting.col.status")}</th>
                    <th className="p-2" />
                  </tr>
                </thead>
                <tbody>
                  {reports.data.map((r) => (
                    <tr key={r.id} className="border-t border-neutral-100 align-top dark:border-neutral-800">
                      <td className="whitespace-nowrap p-2">
                        {formatDate(locale, r.period_start)} – {formatDate(locale, r.period_end)}
                      </td>
                      <td className="p-2">{r.client_company_id ? (clientName.get(r.client_company_id) ?? "…") : t("reporting.all_clients")}</td>
                      <td className="p-2 text-right tabular-nums">{r.document_count}</td>
                      <td className="p-2 text-xs">
                        {Object.entries(r.totals.by_currency ?? {}).map(([cur, v]) => (
                          <span key={cur} className="block">
                            {cur}: {v.net} / {v.vat} / {v.gross}
                          </span>
                        ))}
                      </td>
                      <td className="p-2">
                        <Badge tone={r.status === "submitted" ? "success" : "neutral"}>{t(`reporting.status.${r.status}` as "reporting.status.draft")}</Badge>
                        {r.submitted_ref ? <span className="block text-xs text-neutral-500">{t("reporting.submitted", { ref: r.submitted_ref })}</span> : null}
                      </td>
                      <td className="whitespace-nowrap p-2">
                        <a className="mr-2 text-xs underline" href={`${API_URL}/api/v1/reporting/vida/${r.id}/download?format=json`}>
                          {t("reporting.download.json")}
                        </a>
                        <a className="mr-2 text-xs underline" href={`${API_URL}/api/v1/reporting/vida/${r.id}/download?format=csv`}>
                          {t("reporting.download.csv")}
                        </a>
                        {r.status === "draft" ? (
                          <Button size="sm" disabled={busy || !licensed} onClick={() => run(async () => void (await post(`/api/v1/reporting/vida/${r.id}/submit`)))}>
                            {t("reporting.submit")}
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </Card>
        </div>
        <Card title={t("reporting.generate")}>
          <form onSubmit={generate}>
            <Field id="rep-client" label={t("reporting.client")}>
              <Select id="rep-client" value={clientId} onChange={(e) => setClientId(e.target.value)}>
                <option value="">{t("reporting.all_clients")}</option>
                {(clients.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field id="rep-start" label={t("reporting.period_start")}>
              <Input id="rep-start" type="date" required value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field id="rep-end" label={t("reporting.period_end")}>
              <Input id="rep-end" type="date" required value={end} onChange={(e) => setEnd(e.target.value)} />
            </Field>
            <Button type="submit" disabled={busy || !licensed}>
              {t("reporting.generate")}
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
