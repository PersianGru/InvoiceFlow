"use client";
// Platform settings: the root domain, mail (SMTP/IMAP), the inbound-mail webhook token and
// the AI engines. Firms inherit these defaults unless they set their own.
import { useState, type FormEvent } from "react";

import { BotsCard } from "@/components/bots-card";
import { DomainCard, type DomainView } from "@/components/domain-card";
import { SettingsSection, type SectionView } from "@/components/settings-section";
import { Button, Card, Field, Input, Notice, Spinner } from "@/components/ui";
import { ApiError, get, post, put } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

interface PlatformSettings {
  sections: Record<string, SectionView>;
  root_domain: DomainView | null;
}

export default function PlatformSettingsPage() {
  const { t } = useApp();
  const data = useFetch<PlatformSettings>(() => get<PlatformSettings>("/api/v1/platform/settings"), []);
  const [domain, setDomain] = useState("");
  const [testTo, setTestTo] = useState("");
  const [flash, setFlash] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  if (!data.data) return data.error ? <Notice tone="danger">{data.error}</Notice> : <Spinner label={t("common.loading")} />;
  const s = data.data;

  async function run<T>(fn: () => Promise<T>, ok?: (r: T) => { tone: "success" | "danger" | "warning"; text: string }) {
    setBusy(true);
    setFlash(null);
    try {
      const r = await fn();
      if (ok) setFlash(ok(r));
      await data.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    } finally {
      setBusy(false);
    }
  }

  async function saveDomain(e: FormEvent) {
    e.preventDefault();
    await run(() => put("/api/v1/platform/domain", { domain, grace_days: 0 }), () => ({ tone: "success", text: t("common.saved") }));
  }

  const result = (r: { ok: boolean; message: string }) => ({ tone: r.ok ? ("success" as const) : ("danger" as const), text: r.message });

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{t("platform.nav.settings")}</h1>
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"}>
          {flash.text}
        </Notice>
      ) : null}
      <Card title={t("domain.root_title")}>
        <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">{t("domain.root_intro")}</p>
        {s.root_domain ? <DomainCard d={s.root_domain} busy={busy} onCheck={() => void run(() => post("/api/v1/platform/domain/check"))} /> : null}
        <form onSubmit={saveDomain} className="mt-3 flex flex-wrap items-end gap-2">
          <Field id="root" label={s.root_domain ? t("domain.change_root") : t("domain.set_root")}>
            <Input id="root" placeholder="example.com" value={domain} onChange={(e) => setDomain(e.target.value)} />
          </Field>
          <Button type="submit" disabled={busy || !domain} className="mb-3">
            {t("common.save")}
          </Button>
        </form>
      </Card>
      {s.sections.smtp ? (
        <SettingsSection
          view={s.sections.smtp}
          title={t("settings.smtp")}
          intro={t("settings.smtp_intro")}
          path="/api/v1/platform/settings/smtp"
          onSaved={data.reload}
          extra={
            <span className="flex items-center gap-2">
              <Input aria-label={t("settings.test_to")} placeholder={t("settings.test_to")} type="email" value={testTo} onChange={(e) => setTestTo(e.target.value)} className="w-56" />
              <Button variant="secondary" disabled={busy || !testTo} onClick={() => void run(() => post<{ ok: boolean; message: string }>("/api/v1/platform/settings/smtp/test", { to: testTo }), result)}>
                {t("settings.test_mail")}
              </Button>
            </span>
          }
        />
      ) : null}
      {s.sections.imap ? (
        <SettingsSection
          view={s.sections.imap}
          title={t("settings.imap")}
          intro={t("settings.imap_intro")}
          path="/api/v1/platform/settings/imap"
          onSaved={data.reload}
          extra={
            <Button variant="secondary" disabled={busy} onClick={() => void run(() => post<{ ok: boolean; message: string }>("/api/v1/platform/settings/imap/test"), result)}>
              {t("settings.test_imap")}
            </Button>
          }
        />
      ) : null}
      {s.sections.inbound_webhook ? (
        <SettingsSection view={s.sections.inbound_webhook} title={t("settings.inbound_webhook")} intro={t("settings.inbound_webhook_intro")} path="/api/v1/platform/settings/inbound_webhook" onSaved={data.reload} />
      ) : null}
      <BotsCard base="/api/v1/platform/channel-bots" />
      {s.sections.ai ? <SettingsSection view={s.sections.ai} title={t("settings.ai")} intro={t("settings.ai_intro")} path="/api/v1/platform/settings/ai" onSaved={data.reload} /> : null}
    </div>
  );
}
