"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ApiError, get, post, put, type ClientCompany, type ClientCredential, type ConnectorInfo, type ConnectorRun } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

type Flash = { tone: "success" | "danger"; text: string } | null;

function useRunner(onChange: () => Promise<void>) {
  const { t, refreshLicense } = useApp();
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState(false);
  async function run(fn: () => Promise<string | void>) {
    setBusy(true);
    setFlash(null);
    try {
      const text = await fn();
      if (text) setFlash({ tone: "success", text });
      await onChange();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
    }
  }
  return { run, flash, setFlash, busy };
}

function ConnectorCard({ c, clients, onChange }: { c: ConnectorInfo; clients: ClientCompany[]; onChange: () => Promise<void> }) {
  const { t, locale } = useApp();
  const { run, flash, setFlash, busy } = useRunner(onChange);
  const [form, setForm] = useState<{ enabled: boolean; auto_push: boolean; receive: boolean; config: Record<string, string> }>({
    enabled: c.enabled,
    auto_push: c.auto_push,
    receive: c.receive,
    config: Object.fromEntries(c.fields.map((f) => [f.key, f.secret ? "" : String(c.config[f.key] ?? f.default ?? "")])),
  });
  const canReceive = c.capabilities.includes("receive");

  async function save(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await put(`/api/v1/connectors/${c.key}`, form);
      return t("connectors.saved");
    });
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          {c.name} {c.enabled ? <Badge tone="success">{t("connectors.enabled")}</Badge> : <Badge tone="neutral">{t("connectors.disabled")}</Badge>}
          {c.receive ? <Badge tone="info">{t("connectors.receive_now")}</Badge> : null}
        </span>
      }
    >
      <p className="mb-2 text-sm text-neutral-600">
        {c.description}{" "}
        <a className="underline" href={c.docs_url} target="_blank" rel="noreferrer">
          {t("connectors.docs")}
        </a>
      </p>
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      {c.last_test_at ? (
        <p className="mb-2 text-xs text-neutral-600">
          {t("connectors.last_test")}: {c.last_test_ok ? "✓" : "✗"} {c.last_test_message} ({formatDateTime(locale, c.last_test_at)})
        </p>
      ) : null}
      <form onSubmit={save}>
        {c.fields.map((f) => (
          <Field key={f.key} id={`${c.key}-${f.key}`} label={f.label} hint={f.secret && c.config[f.key] ? t("connectors.secret_stored") : f.hint || undefined}>
            <Input id={`${c.key}-${f.key}`} type={f.secret ? "password" : "text"} autoComplete="off" value={form.config[f.key] ?? ""} onChange={(e) => setForm({ ...form, config: { ...form.config, [f.key]: e.target.value } })} />
          </Field>
        ))}
        <label className="mb-2 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
          {t("connectors.enable")}
        </label>
        <label className="mb-2 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.auto_push} disabled={!form.enabled} onChange={(e) => setForm({ ...form, auto_push: e.target.checked })} />
          {t("connectors.auto_push")}
        </label>
        {canReceive ? (
          <label className="mb-3 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.receive} disabled={!form.enabled} onChange={(e) => setForm({ ...form, receive: e.target.checked })} />
            {t("connectors.receive")}
          </label>
        ) : null}
        {canReceive && c.routing_field ? <p className="mb-3 text-xs text-neutral-600">{t("connectors.routing", { field: t(c.routing_field === "peppol_id" ? "clients.peppol_id" : "clients.tax_number") })}</p> : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy}>
            {t("common.save")}
          </Button>
          <Button
            variant="secondary"
            disabled={busy || !c.enabled}
            onClick={() =>
              run(async () => {
                const r = await post<{ ok: boolean; message: string }>(`/api/v1/connectors/${c.key}/test`);
                setFlash(r.ok ? { tone: "success", text: t("connectors.test_ok") } : { tone: "danger", text: t("connectors.test_failed") });
              })
            }
          >
            {t("connectors.test")}
          </Button>
          {canReceive ? (
            <Button
              variant="secondary"
              disabled={busy || !c.receive}
              onClick={() =>
                run(async () => {
                  const r = await post<{ stored: number; quarantined: number; duplicates: number }>(`/api/v1/connectors/${c.key}/receive`);
                  return t("connectors.received", { stored: r.stored, quarantined: r.quarantined, duplicates: r.duplicates });
                })
              }
            >
              {t("connectors.receive_now")}
            </Button>
          ) : null}
        </div>
      </form>
      {canReceive && c.receive_scope === "client" && c.enabled ? <ClientCredentials c={c} clients={clients} /> : null}
    </Card>
  );
}

function ClientCredentials({ c, clients }: { c: ConnectorInfo; clients: ClientCompany[] }) {
  const { t, locale } = useApp();
  const creds = useFetch<ClientCredential[]>(() => get<ClientCredential[]>(`/api/v1/connectors/${c.key}/clients`), [c.key]);
  const { run, flash, busy } = useRunner(async () => void (await creds.reload()));
  const byClient = new Map((creds.data ?? []).map((x) => [x.client_company_id, x]));
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({});

  return (
    <div className="mt-4 border-t border-neutral-100 pt-3 dark:border-neutral-800">
      <h3 className="mb-1 text-sm font-semibold">{t("connectors.clients")}</h3>
      <p className="mb-2 text-xs text-neutral-600">{t("connectors.clients.help")}</p>
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      <ul className="space-y-3">
        {clients.map((client) => {
          const cred = byClient.get(client.id);
          const draft = drafts[client.id] ?? {};
          return (
            <li key={client.id} className="rounded-md border border-neutral-200 p-2 text-sm dark:border-neutral-800">
              <div className="mb-1 flex items-center gap-2">
                <span className="font-medium">{client.name}</span>
                {cred?.enabled ? <Badge tone="success">{t("connectors.enabled")}</Badge> : null}
                {cred?.last_pull_at ? <span className="text-xs text-neutral-500">{t("connectors.clients.last_pull")}: {formatDateTime(locale, cred.last_pull_at)}</span> : null}
                {cred?.last_error ? <span className="text-xs text-red-700">{cred.last_error}</span> : null}
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {c.client_fields.map((f) => (
                  <Field key={f.key} id={`${c.key}-${client.id}-${f.key}`} label={f.label} hint={f.secret && cred?.config[f.key] ? t("connectors.secret_stored") : f.hint || undefined}>
                    <Input
                      id={`${c.key}-${client.id}-${f.key}`}
                      type={f.secret ? "password" : "text"}
                      autoComplete="off"
                      value={draft[f.key] ?? (f.secret ? "" : (cred?.config[f.key] ?? ""))}
                      onChange={(e) => setDrafts({ ...drafts, [client.id]: { ...draft, [f.key]: e.target.value } })}
                    />
                  </Field>
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await put(`/api/v1/connectors/${c.key}/clients/${client.id}`, { enabled: true, config: draft });
                      setDrafts({ ...drafts, [client.id]: {} });
                      return t("connectors.saved");
                    })
                  }
                >
                  {t("connectors.clients.enabled")}
                </Button>
                {cred?.enabled ? (
                  <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(async () => void (await put(`/api/v1/connectors/${c.key}/clients/${client.id}`, { enabled: false, config: {} })))}>
                    {t("connectors.disabled")}
                  </Button>
                ) : null}
                {cred ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        const r = await post<{ ok: boolean; message: string }>(`/api/v1/connectors/${c.key}/clients/${client.id}/test`);
                        return r.ok ? t("connectors.test_ok") : `${t("connectors.test_failed")} ${r.message}`;
                      })
                    }
                  >
                    {t("connectors.test")}
                  </Button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function ConnectorsPage() {
  const { t, locale, license } = useApp();
  const connectors = useFetch<ConnectorInfo[]>(() => get<ConnectorInfo[]>("/api/v1/connectors"), []);
  const runs = useFetch<ConnectorRun[]>(() => get<ConnectorRun[]>("/api/v1/connectors/runs", { limit: 30 }), []);
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const entitled = license?.features?.connectors === true;
  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">{t("connectors.title")}</h1>
      <p className="mb-3 text-sm text-neutral-600">{t("connectors.help")}</p>
      {license && !entitled ? (
        <Notice tone="warning" className="mb-3">
          {t("connectors.not_entitled")}
        </Notice>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        {(connectors.data ?? []).map((c) => (
          <ConnectorCard key={c.key + String(c.enabled) + String(c.receive) + String(c.last_test_at)} c={c} clients={clients.data ?? []} onChange={async () => Promise.all([connectors.reload(), runs.reload()]).then(() => undefined)} />
        ))}
      </div>
      <h2 className="mb-2 mt-6 text-lg font-semibold">{t("connectors.runs")}</h2>
      <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
            <tr>
              <th className="p-2">{t("common.created")}</th>
              <th className="p-2">{t("connectors.connector")}</th>
              <th className="p-2">{t("inbox.col.number")}</th>
              <th className="p-2">{t("common.status")}</th>
              <th className="p-2">{t("connectors.message")}</th>
            </tr>
          </thead>
          <tbody>
            {(runs.data ?? []).map((r) => (
              <tr key={r.id} className="border-t border-neutral-100 dark:border-neutral-800">
                <td className="whitespace-nowrap p-2">{formatDateTime(locale, r.created_at)}</td>
                <td className="p-2">
                  {r.key} <span className="text-xs text-neutral-500">({r.trigger})</span>
                </td>
                <td className="p-2 font-mono text-xs">{r.document_id ? r.document_id.slice(0, 8) : "—"}</td>
                <td className="p-2">
                  <Badge tone={r.status === "ok" ? "success" : r.status === "error" ? "danger" : "neutral"}>{r.status}</Badge>
                </td>
                <td className="p-2 text-neutral-600">
                  {r.message}
                  {r.external_id ? ` · ${r.external_id}` : ""}
                </td>
              </tr>
            ))}
            {runs.data && runs.data.length === 0 ? (
              <tr>
                <td className="p-6 text-center text-neutral-600" colSpan={5}>
                  {t("connectors.runs.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
