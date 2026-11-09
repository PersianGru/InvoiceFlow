"use client";
// Package D: own Telegram bots and WhatsApp numbers for the firm (or the platform), optionally
// dedicated to one client company. Tokens are write-only; the webhook is registered automatically for
// Telegram and shown (with the one-time verify token) for WhatsApp.
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Select } from "@/components/ui";
import { ApiError, del, get, post, type ClientCompany } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

interface BotView {
  id: string;
  channel: "telegram" | "whatsapp";
  client_company_id: string | null;
  display_name: string;
  username: string | null;
  phone_number_id: string | null;
  accept_from: string;
  active: boolean;
  last_error: string | null;
  webhook_url: string;
  verify_token?: string;
}

export function BotsCard({ base, clients }: { base: "/api/v1/channel-bots" | "/api/v1/platform/channel-bots"; clients?: ClientCompany[] }) {
  const { t } = useApp();
  const bots = useFetch<BotView[]>(() => get<BotView[]>(base), [base]);
  const [form, setForm] = useState({ channel: "telegram", client_company_id: "", token: "", access_token: "", phone_number_id: "", app_secret: "", accept_from: "anyone" });
  const [created, setCreated] = useState<BotView | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const name = (id: string | null) => (clients ?? []).find((c) => c.id === id)?.name;

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFlash(null);
    try {
      const body =
        form.channel === "telegram"
          ? { channel: "telegram", token: form.token, client_company_id: form.client_company_id || null, accept_from: form.accept_from }
          : { channel: "whatsapp", access_token: form.access_token, phone_number_id: form.phone_number_id, app_secret: form.app_secret, client_company_id: form.client_company_id || null, accept_from: form.accept_from };
      const r = await post<BotView>(base, body);
      setCreated(r);
      setForm({ ...form, token: "", access_token: "", app_secret: "" });
      await bots.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t("bots.title")}>
      <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">{t("bots.intro")}</p>
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      {created ? (
        <Notice tone="success" className="mb-3">
          {created.channel === "telegram" ? (
            <p>{created.last_error ? t("bots.webhook_failed") : t("bots.telegram_ready", { name: created.username ?? created.display_name })}</p>
          ) : (
            <>
              <p>{t("bots.whatsapp_setup")}</p>
              <p className="mt-1 break-all font-mono text-xs">{created.webhook_url}</p>
              <p className="mt-1 break-all font-mono text-xs">verify token: {created.verify_token}</p>
            </>
          )}
        </Notice>
      ) : null}
      <ul className="mb-3 space-y-2 text-sm">
        {(bots.data ?? []).map((b) => (
          <li key={b.id} className="flex flex-wrap items-center gap-2">
            <Badge>{b.channel}</Badge>
            <span className="font-medium">{b.username ? `@${b.username}` : b.phone_number_id ?? b.display_name}</span>
            {b.client_company_id ? <Badge tone="info">{t("bots.dedicated", { client: name(b.client_company_id) ?? "…" })}</Badge> : <Badge>{t("bots.shared")}</Badge>}
            {b.last_error ? <Badge tone="warning">{b.last_error}</Badge> : null}
            <Button
              size="sm"
              variant="danger"
              onClick={async () => {
                await del(`${base}/${b.id}`);
                await bots.reload();
              }}
            >
              {t("common.delete")}
            </Button>
          </li>
        ))}
      </ul>
      <form onSubmit={create} className="grid gap-x-3 md:grid-cols-2">
        <Field id="b-channel" label={t("inbox.col.channel")}>
          <Select id="b-channel" value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
            <option value="telegram">Telegram</option>
            <option value="whatsapp">WhatsApp</option>
          </Select>
        </Field>
        {clients ? (
          <Field id="b-client" label={t("bots.for_client")}>
            <Select id="b-client" value={form.client_company_id} onChange={(e) => setForm({ ...form, client_company_id: e.target.value })}>
              <option value="">{t("bots.all_clients")}</option>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}
        {form.channel === "telegram" ? (
          <Field id="b-token" label={t("bots.token")} hint={t("bots.token_hint")}>
            <Input id="b-token" type="password" autoComplete="off" required value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} />
          </Field>
        ) : (
          <>
            <Field id="b-pnid" label={t("bots.phone_number_id")}>
              <Input id="b-pnid" required value={form.phone_number_id} onChange={(e) => setForm({ ...form, phone_number_id: e.target.value })} />
            </Field>
            <Field id="b-at" label={t("bots.access_token")}>
              <Input id="b-at" type="password" autoComplete="off" required value={form.access_token} onChange={(e) => setForm({ ...form, access_token: e.target.value })} />
            </Field>
            <Field id="b-as" label={t("bots.app_secret")}>
              <Input id="b-as" type="password" autoComplete="off" required value={form.app_secret} onChange={(e) => setForm({ ...form, app_secret: e.target.value })} />
            </Field>
          </>
        )}
        {form.client_company_id ? (
          <Field id="b-accept" label={t("bots.accept_from")}>
            <Select id="b-accept" value={form.accept_from} onChange={(e) => setForm({ ...form, accept_from: e.target.value })}>
              <option value="anyone">{t("bots.accept_anyone")}</option>
              <option value="paired">{t("bots.accept_paired")}</option>
            </Select>
          </Field>
        ) : null}
        <div className="md:col-span-2">
          <Button type="submit" disabled={busy}>
            {t("bots.add")}
          </Button>
        </div>
      </form>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer">{t("bots.howto")}</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5">
          <li>{t("bots.howto1")}</li>
          <li>{t("bots.howto2")}</li>
          <li>{t("bots.howto3")}</li>
          <li>{t("bots.howto4")}</li>
        </ol>
      </details>
    </Card>
  );
}
