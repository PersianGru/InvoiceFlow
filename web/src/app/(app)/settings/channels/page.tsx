"use client";
import { useState, type FormEvent } from "react";

import { BotsCard } from "@/components/bots-card";
import { Badge, Button, Card, Field, Input, Notice, Select } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ApiError, API_URL, del, get, patch, post, type ChannelStatus, type ClientCompany, type SenderMapping } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

const CHANNELS = ["email", "telegram", "whatsapp"] as const;

export default function ChannelsPage() {
  const { t, locale, license, refreshLicense } = useApp();
  const status = useFetch<ChannelStatus>(() => get<ChannelStatus>("/api/v1/channels/status"), []);
  const mappings = useFetch<SenderMapping[]>(() => get<SenderMapping[]>("/api/v1/sender-mappings"), []);
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const [form, setForm] = useState({ client_company_id: "", channel: "email" as (typeof CHANNELS)[number], identity: "", display_name: "" });
  const [pair, setPair] = useState({ client_company_id: "", channel: "telegram" as "telegram" | "whatsapp" });
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [code, setCode] = useState<SenderMapping | null>(null);
  const clientName = new Map((clients.data ?? []).map((c) => [c.id, c.name]));
  const channelsOn = license?.features?.channels === true;

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setFlash(null);
    try {
      await fn();
      if (ok) setFlash({ tone: "success", text: ok });
      await mappings.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    }
  }

  async function addMapping(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await post("/api/v1/sender-mappings", form);
      setForm({ ...form, identity: "", display_name: "" });
    }, t("channels.mapping.saved"));
  }

  async function newCode(e: FormEvent) {
    e.preventDefault();
    await run(async () => setCode(await post<SenderMapping>("/api/v1/sender-mappings/pairing-code", pair)));
  }

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">{t("channels.title")}</h1>
      <p className="mb-3 text-sm text-neutral-600">{t("channels.help")}</p>
      {license && !channelsOn ? (
        <Notice tone="warning" className="mb-3">
          {t("channels.not_entitled")}
        </Notice>
      ) : null}
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      <div className="mb-4 grid gap-3 md:grid-cols-3">
        {status.data
          ? CHANNELS.map((ch) => {
              const s = status.data![ch];
              return (
                <Card key={ch} title={t(`channels.${ch}` as "channels.email")}>
                  <p className="mb-1">{s.configured ? <Badge tone="success">{t("channels.configured")}</Badge> : <Badge tone="neutral">{t("channels.not_configured")}</Badge>}</p>
                  {ch === "email" ? (
                    <p className="text-xs text-neutral-600">
                      {t("channels.email.domain")}: <code>@{status.data!.email.domain}</code>
                    </p>
                  ) : null}
                  <p className="break-all text-xs text-neutral-600">
                    {t("channels.webhook")}: <code>{`${API_URL}${s.webhook_path}`}</code>
                  </p>
                </Card>
              );
            })
          : null}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <h2 className="mb-2 text-lg font-semibold">{t("channels.mappings")}</h2>
          <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                <tr>
                  <th className="p-2">{t("inbox.filter.client")}</th>
                  <th className="p-2">{t("inbox.col.channel")}</th>
                  <th className="p-2">{t("channels.identity")}</th>
                  <th className="p-2">{t("channels.paired")}</th>
                  <th className="p-2">{t("channels.last_seen")}</th>
                  <th className="p-2" />
                </tr>
              </thead>
              <tbody>
                {(mappings.data ?? []).map((m) => (
                  <tr key={m.id} className="border-t border-neutral-100 dark:border-neutral-800">
                    <td className="p-2">{clientName.get(m.client_company_id) ?? "…"}</td>
                    <td className="p-2">
                      {m.channel}
                      {m.scope === "chat" ? (
                        <span className="ml-1">
                          <Badge tone="info">{t("channels.chat")}</Badge>
                        </span>
                      ) : null}
                      {m.active === false ? (
                        <span className="ml-1">
                          <Badge tone="warning">{t("channels.inactive")}</Badge>
                        </span>
                      ) : null}
                    </td>
                    <td className="p-2 font-mono text-xs">
                      {m.scope === "chat" && m.display_name ? <span className="font-sans">{m.display_name} · </span> : null}
                      {m.identity ?? (m.pairing_code ? `${t("channels.code")} ${m.pairing_code}` : "—")}
                      {m.scope === "chat" ? (
                        <label className="mt-1 flex items-center gap-1 font-sans">
                          <input
                            type="checkbox"
                            checked={m.accept_from === "paired"}
                            onChange={(e) => run(() => patch(`/api/v1/sender-mappings/${m.id}`, { accept_from: e.target.checked ? "paired" : "anyone" }), t("common.saved"))}
                          />
                          {t("channels.members_only")}
                        </label>
                      ) : null}
                    </td>
                    <td className="p-2">{m.paired_at ? <Badge tone="success">{t("common.yes")}</Badge> : <Badge tone="warning">{t("channels.pending")}</Badge>}</td>
                    <td className="whitespace-nowrap p-2">{m.last_seen_at ? formatDateTime(locale, m.last_seen_at) : t("common.none")}</td>
                    <td className="p-2 text-right">
                      <Button size="sm" variant="danger" onClick={() => run(() => del(`/api/v1/sender-mappings/${m.id}`), t("channels.mapping.deleted"))}>
                        {t("common.delete")}
                      </Button>
                    </td>
                  </tr>
                ))}
                {mappings.data && mappings.data.length === 0 ? (
                  <tr>
                    <td className="p-6 text-center text-neutral-600" colSpan={6}>
                      {t("channels.mappings.empty")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
        <div className="space-y-4">
          <Card title={t("channels.mapping.new")}>
            <form onSubmit={addMapping}>
              <Field id="m-client" label={t("inbox.filter.client")}>
                <Select id="m-client" required value={form.client_company_id} onChange={(e) => setForm({ ...form, client_company_id: e.target.value })}>
                  <option value="">—</option>
                  {(clients.data ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field id="m-channel" label={t("inbox.col.channel")}>
                <Select id="m-channel" value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value as (typeof CHANNELS)[number] })}>
                  {CHANNELS.map((c) => (
                    <option key={c} value={c}>
                      {t(`channels.${c}` as "channels.email")}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field id="m-identity" label={t("channels.identity")} hint={t("channels.identity.hint")}>
                <Input id="m-identity" required value={form.identity} onChange={(e) => setForm({ ...form, identity: e.target.value })} />
              </Field>
              <Field id="m-name" label={t("common.name")}>
                <Input id="m-name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
              </Field>
              <Button type="submit">{t("common.save")}</Button>
            </form>
          </Card>
          <Card title={t("channels.pairing.title")}>
            <p className="mb-2 text-sm text-neutral-600">{t("channels.pairing.help")}</p>
            <form onSubmit={newCode}>
              <Field id="p-client" label={t("inbox.filter.client")}>
                <Select id="p-client" required value={pair.client_company_id} onChange={(e) => setPair({ ...pair, client_company_id: e.target.value })}>
                  <option value="">—</option>
                  {(clients.data ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field id="p-channel" label={t("inbox.col.channel")}>
                <Select id="p-channel" value={pair.channel} onChange={(e) => setPair({ ...pair, channel: e.target.value as "telegram" | "whatsapp" })}>
                  <option value="telegram">Telegram</option>
                  <option value="whatsapp">WhatsApp</option>
                </Select>
              </Field>
              <Button type="submit" disabled={license !== null && !channelsOn}>
                {t("channels.pairing.new")}
              </Button>
            </form>
            {code ? (
              <Notice tone="success" className="mt-3">
                <p className="text-lg font-mono">{code.pairing_code}</p>
                <p className="text-xs">{t("channels.pairing.instructions", { code: code.pairing_code ?? "", channel: code.channel })}</p>
              </Notice>
            ) : null}
          </Card>
          <BotsCard base="/api/v1/channel-bots" clients={clients.data ?? []} />
        </div>
      </div>
    </div>
  );
}
