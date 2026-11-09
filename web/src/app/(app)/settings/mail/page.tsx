"use client";
// The firm's mail settings and inbound domains: the platform subdomain (renamable), the
// firm's own verified domain, and SMTP/IMAP of its own or the platform default.
import { useState, type FormEvent } from "react";

import { DomainCard, type DomainView } from "@/components/domain-card";
import { SettingsSection, type SectionView } from "@/components/settings-section";
import { Button, Card, Field, Input, Notice, Spinner } from "@/components/ui";
import { ApiError, del, get, patch, post, put } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

interface FirmSettings {
  sections: Record<string, SectionView>;
  domains: DomainView[];
  root_domain: string | null;
  legacy_domain: string | null;
}

export default function MailSettingsPage() {
  const { t } = useApp();
  const data = useFetch<FirmSettings>(() => get<FirmSettings>("/api/v1/firm-settings"), []);
  const [label, setLabel] = useState("");
  const [custom, setCustom] = useState("");
  const [grace, setGrace] = useState(0);
  const [testTo, setTestTo] = useState("");
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  if (!data.data) return data.error ? <Notice tone="danger">{data.error}</Notice> : <Spinner label={t("common.loading")} />;
  const s = data.data;
  const active = s.domains.filter((d) => !d.retired_at);
  const retired = s.domains.filter((d) => d.retired_at);
  const sub = active.find((d) => d.kind === "platform_sub");
  const own = active.find((d) => d.kind === "custom");

  async function run<T>(fn: () => Promise<T>, ok?: (r: T) => { tone: "success" | "danger"; text: string }) {
    setBusy(true);
    setFlash(null);
    try {
      const r = await fn();
      setFlash(ok ? ok(r) : { tone: "success", text: t("common.saved") });
      await data.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    } finally {
      setBusy(false);
    }
  }

  const result = (r: { ok: boolean; message: string }) => ({ tone: r.ok ? ("success" as const) : ("danger" as const), text: r.message });

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{t("nav.settings.mail")}</h1>
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"}>
          {flash.text}
        </Notice>
      ) : null}
      <Card title={t("domain.firm_title")}>
        <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">{t("domain.firm_intro")}</p>
        {s.legacy_domain ? <Notice className="mb-3">{t("domain.legacy", { domain: s.legacy_domain })}</Notice> : null}
        <div className="grid gap-3 md:grid-cols-2">
          {sub ? <DomainCard d={sub} /> : null}
          {own ? <DomainCard d={own} busy={busy} onCheck={() => void run(() => post("/api/v1/firm-settings/domains/custom/check"))} /> : null}
        </div>
        <Field id="grace" label={t("domain.grace")} hint={t("domain.grace_hint")}>
          <Input id="grace" type="number" min={0} max={14} value={grace} onChange={(e) => setGrace(Number(e.target.value))} className="w-24" />
        </Field>
        {s.root_domain ? (
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              void run(() => patch("/api/v1/firm-settings/domains/subdomain", { label, grace_days: grace }));
            }}
          >
            <Field id="label" label={t("domain.rename", { root: s.root_domain })}>
              <Input id="label" placeholder={sub?.label ?? ""} value={label} onChange={(e) => setLabel(e.target.value)} />
            </Field>
            <Button type="submit" className="mb-3" disabled={busy || !label}>
              {t("domain.rename_submit")}
            </Button>
          </form>
        ) : null}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            void run(() => put("/api/v1/firm-settings/domains/custom", { domain: custom, grace_days: grace }));
          }}
        >
          <Field id="custom" label={t("domain.custom")}>
            <Input id="custom" placeholder="in.example.com" value={custom} onChange={(e) => setCustom(e.target.value)} />
          </Field>
          <Button type="submit" className="mb-3" disabled={busy || !custom}>
            {t("common.save")}
          </Button>
          {own ? (
            <Button variant="secondary" className="mb-3" disabled={busy} onClick={() => void run(() => del(`/api/v1/firm-settings/domains/custom?grace_days=${grace}`))}>
              {t("domain.remove_custom")}
            </Button>
          ) : null}
        </form>
        {retired.length ? (
          <details className="mt-2 text-sm">
            <summary className="cursor-pointer">{t("domain.retired_list", { n: retired.length })}</summary>
            <div className="mt-2 grid gap-2">
              {retired.map((d) => (
                <DomainCard key={d.id} d={d} />
              ))}
            </div>
          </details>
        ) : null}
      </Card>
      {s.sections.smtp ? (
        <SettingsSection
          view={s.sections.smtp}
          title={t("settings.smtp")}
          intro={t("settings.firm_smtp_intro")}
          path="/api/v1/firm-settings/smtp"
          onSaved={data.reload}
          extra={
            <span className="flex flex-wrap items-center gap-2">
              <Input aria-label={t("settings.test_to")} placeholder={t("settings.test_to")} type="email" value={testTo} onChange={(e) => setTestTo(e.target.value)} className="w-56" />
              <Button variant="secondary" disabled={busy || !testTo} onClick={() => void run(() => post<{ ok: boolean; message: string }>("/api/v1/firm-settings/smtp/test", { to: testTo }), result)}>
                {t("settings.test_mail")}
              </Button>
              {s.sections.smtp.own ? (
                <Button variant="ghost" disabled={busy} onClick={() => void run(() => del("/api/v1/firm-settings/smtp"))}>
                  {t("settings.use_platform")}
                </Button>
              ) : null}
            </span>
          }
        />
      ) : null}
      {s.sections.imap ? (
        <SettingsSection
          view={s.sections.imap}
          title={t("settings.imap")}
          intro={t("settings.firm_imap_intro")}
          path="/api/v1/firm-settings/imap"
          onSaved={data.reload}
          extra={
            <span className="flex flex-wrap items-center gap-2">
              <Button variant="secondary" disabled={busy} onClick={() => void run(() => post<{ ok: boolean; message: string }>("/api/v1/firm-settings/imap/test"), result)}>
                {t("settings.test_imap")}
              </Button>
              {s.sections.imap.own ? (
                <Button variant="ghost" disabled={busy} onClick={() => void run(() => del("/api/v1/firm-settings/imap"))}>
                  {t("settings.use_platform")}
                </Button>
              ) : null}
            </span>
          }
        />
      ) : null}
    </div>
  );
}
