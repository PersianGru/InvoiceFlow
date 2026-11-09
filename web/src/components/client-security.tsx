"use client";
// Package C: a client company's inbound address (copy, regenerate with justification and optional grace)
// and intake pause per channel. Used on the Clients page.
import { useState } from "react";

import { useReason } from "@/components/reason-dialog";
import { Badge, Button, Input } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ApiError, post, type ClientCompany } from "@/lib/api";
import { useApp } from "@/lib/app-context";

const CHANNELS = ["email", "telegram", "whatsapp", "upload", "api"] as const;

export function ClientSecurity({ client, onChanged, onError }: { client: ClientCompany; onChanged: () => Promise<void>; onError: (text: string) => void }) {
  const { t, locale } = useApp();
  const [grace, setGrace] = useState(0);
  const [dialog, ask] = useReason();
  const paused = client.intake_paused ?? {};

  async function regenerate() {
    const reason = await ask(t("clients.regenerate"), { body: t("clients.regenerate_body", { days: grace }), danger: true });
    if (!reason) return;
    try {
      await post(`/api/v1/client-companies/${client.id}/inbound-address/regenerate`, { reason, grace_days: grace });
      await onChanged();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  async function toggle(channel: (typeof CHANNELS)[number]) {
    const next = !paused[channel];
    const reason = await ask(next ? t("clients.pause", { channel: t(`channel.${channel}` as "channel.email") }) : t("clients.resume", { channel: t(`channel.${channel}` as "channel.email") }));
    if (!reason) return;
    try {
      await post(`/api/v1/client-companies/${client.id}/intake-pause`, { channels: { [channel]: next }, reason });
      await onChanged();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  return (
    <div className="space-y-3 text-sm">
      {dialog}
      <div>
        <p className="mb-1 font-medium">{t("clients.inbound")}</p>
        <div className="flex flex-wrap items-center gap-2">
          <code className="rounded bg-neutral-100 px-2 py-1 dark:bg-neutral-800">{client.inbound_email_address ?? client.inbound_email_local_part ?? "—"}</code>
          {client.inbound_email_address ? (
            <Button size="sm" variant="ghost" onClick={() => void navigator.clipboard?.writeText(client.inbound_email_address ?? "")}>
              {t("common.copy")}
            </Button>
          ) : null}
          <label className="flex items-center gap-1 text-xs">
            {t("clients.grace_days")}
            <Input type="number" min={0} max={14} value={grace} onChange={(e) => setGrace(Number(e.target.value))} className="w-16 py-0.5" />
          </label>
          <Button size="sm" variant="danger" onClick={() => void regenerate()}>
            {t("clients.regenerate")}
          </Button>
        </div>
        {(client.retired_addresses ?? []).length ? (
          <ul className="mt-2 space-y-1 text-xs text-neutral-600 dark:text-neutral-400">
            {(client.retired_addresses ?? []).map((r) => (
              <li key={r.address}>
                <span className="font-mono line-through">{r.address}</span> · {r.retired_at ? formatDateTime(locale, r.retired_at) : ""} · “{r.reason}”
                {r.grace_until ? ` · ${t("domain.grace_until", { date: formatDateTime(locale, r.grace_until) })}` : ""}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div>
        <p className="mb-1 font-medium">{t("clients.intake")}</p>
        <div className="flex flex-wrap gap-2">
          {CHANNELS.map((ch) => (
            <button key={ch} type="button" onClick={() => void toggle(ch)} className="rounded-full" aria-pressed={Boolean(paused[ch])}>
              <Badge tone={paused[ch] ? "danger" : "success"}>
                {t(`channel.${ch}` as "channel.email")}: {paused[ch] ? t("clients.paused") : t("clients.open")}
              </Badge>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
