"use client";
// The License page: state, plan, trial days, licensed to, buy/renew links with
// the installation id, enter a key, import a .lic file, offline request code (text + QR),
// deactivate, support messages. Never shows the key or the token.
import { useState } from "react";

import { Badge, Button, Card, Field, Input, Notice, Textarea } from "@/components/ui";
import { formatDate } from "@/i18n";
import { ApiError, post, type LicenseView, type RefreshView } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { entitlementRows, expiresLabel, refreshFlash, stateKey } from "@/lib/license";

type Flash = { tone: "success" | "danger" | "warning"; text: string } | null;

export default function LicensePage() {
  const { t, locale, user, license, refreshLicense } = useApp();
  const [key, setKey] = useState("");
  const [file, setFile] = useState("");
  const [offline, setOffline] = useState<{ request_code: string; qr_svg: string; instructions: string } | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState(false);
  const firmId = user?.firm_id;
  if (!firmId || !license) return null;
  const v: LicenseView = license;
  const tone = v.state === "valid" ? "success" : v.state === "grace" ? "warning" : "danger";

  async function run(label: string, fn: () => Promise<unknown>, okKey: "license.activated" | "license.imported" | "license.deactivated" | null) {
    setBusy(true);
    setFlash(null);
    try {
      const result = await fn();
      await refreshLicense();
      if (label === "refresh") setFlash(refreshFlash((result as RefreshView).outcome, locale));
      else if (okKey) setFlash({ tone: "success", text: t(okKey) });
    } catch (err) {
      const e = err as ApiError;
      const hint = e instanceof ApiError && (e.details as { hint?: string } | undefined)?.hint === "deactivate_other_install" ? ` ${t("license.deactivate_hint")}` : "";
      setFlash({ tone: "danger", text: (e instanceof ApiError ? e.message : t("common.error")) + hint });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{t("license.title")}</h1>
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"}>
          {flash.text}
        </Notice>
      ) : null}
      <Card
        title={
          <span className="flex items-center gap-2">
            {t(stateKey(v))} <Badge tone={tone}>{v.state}</Badge>
            {v.trial ? <Badge tone="info">{t("license.trial_days_left", { days: v.trial_days_left ?? 0 })}</Badge> : null}
          </span>
        }
        actions={
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => run("refresh", () => post<RefreshView>(`/api/v1/firms/${firmId}/license/refresh`), null)}>
            {t("license.refresh")}
          </Button>
        }
      >
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <dt className="text-neutral-600">{t("license.plan")}</dt>
          <dd>{v.plan_name ?? v.plan ?? t("common.none")}</dd>
          <dt className="text-neutral-600">{t("license.licensed_to")}</dt>
          <dd>{v.licensed_to ?? t("common.none")}</dd>
          <dt className="text-neutral-600">{t("license.expires")}</dt>
          <dd>{v.state === "unlicensed" && !v.has_key ? t("common.none") : expiresLabel(v, locale)}</dd>
          <dt className="text-neutral-600">{t("license.key_hint")}</dt>
          <dd className="font-mono">{v.kh ? `…${v.kh}` : t("common.none")}</dd>
          <dt className="text-neutral-600">{t("license.grace_days")}</dt>
          <dd>{v.grace_days}</dd>
          <dt className="text-neutral-600">{t("license.next_refresh")}</dt>
          <dd>{v.next_refresh_at ? formatDate(locale, v.next_refresh_at, "medium") : t("common.none")}</dd>
          <dt className="text-neutral-600">{t("license.installation_id")}</dt>
          <dd>
            <code className="font-mono text-xs">{v.installation_id_grouped}</code>
            <p className="text-xs text-neutral-500">{t("license.installation_id_help")}</p>
          </dd>
          {v.hub_error ? (
            <>
              <dt className="text-neutral-600">{t("license.hub_error")}</dt>
              <dd>
                {v.hub_error}
                {v.hub_message ? ` — ${v.hub_message}` : ""}
              </dd>
            </>
          ) : null}
        </dl>
        <div className="mt-4 flex flex-wrap gap-2">
          {v.state === "unlicensed" && !v.has_key ? (
            <a className="rounded-md bg-blue-700 px-3.5 py-2 text-sm font-medium text-white hover:bg-blue-800" href={v.store_links.trial} target="_blank" rel="noreferrer">
              {t("license.cta.start_trial")}
            </a>
          ) : null}
          <a className="rounded-md border border-neutral-300 px-3.5 py-2 text-sm font-medium hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800" href={v.store_links.buy} target="_blank" rel="noreferrer">
            {v.state === "valid" || v.state === "grace" ? t("license.renew") : t("license.cta.buy")}
          </a>
        </div>
        {v.messages.length ? (
          <div className="mt-4">
            <h3 className="mb-1 text-sm font-semibold">{t("license.messages")}</h3>
            <ul className="space-y-1 text-sm">
              {v.messages.map((m) => (
                <li key={m.id} className="flex items-start gap-2">
                  <Badge tone={m.level === "warning" ? "warning" : "info"}>{m.level}</Badge>
                  <span>{m.text}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Card>

      <Card title={t("license.entitlements")}>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
          {entitlementRows(v).map((r) => (
            <div key={r.key} className="contents">
              <dt className="text-neutral-600">{t(`license.feature.${r.key}` as "license.feature.api")}</dt>
              <dd className="font-medium">{r.value}</dd>
            </div>
          ))}
        </dl>
        {Object.keys(v.items ?? {}).length > 0 ? (
          <table className="mt-3 w-full text-sm">
            <thead className="text-left text-xs uppercase text-neutral-600">
              <tr>
                <th className="py-1">{t("license.tools.tool")}</th>
                <th className="py-1">{t("license.tools.ends")}</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(v.items).map(([code, it]) => (
                <tr key={code} className="border-t border-neutral-100 dark:border-neutral-800">
                  <td className="py-1 font-medium">
                    {code}
                    {it.trial ? <Badge tone="info" className="ml-2">{t("license.tools.trial")}</Badge> : null}
                  </td>
                  <td className="py-1">{it.expires ? formatDate(locale, it.expires) : t("license.tools.never")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {v.store_links.addons ? (
          <p className="mt-3 text-sm">
            <a className="underline" href={v.store_links.addons} target="_blank" rel="noreferrer">
              {t("license.addons.link")}
            </a>{" "}
            <span className="text-neutral-600">{t("license.addons.hint")}</span>
          </p>
        ) : null}
      </Card>

      <Card title={t("license.enter_key")}>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run("activate", () => post(`/api/v1/firms/${firmId}/license/activate`, { key }), "license.activated").then(() => setKey(""));
          }}
        >
          <Field id="key" label={t("license.enter_key")}>
            <Input id="key" value={key} onChange={(e) => setKey(e.target.value)} placeholder={t("license.key_placeholder")} autoComplete="off" spellCheck={false} />
          </Field>
          <Button type="submit" disabled={busy || key.trim().length < 10}>
            {busy ? t("license.activating") : t("license.activate")}
          </Button>
        </form>
      </Card>

      <Card title={t("license.offline.title")}>
        <p className="mb-2 text-sm text-neutral-600">{t("license.offline.help")}</p>
        <div className="mb-3 flex flex-wrap gap-2">
          <Button variant="secondary" disabled={busy} onClick={() => run("offline", async () => setOffline(await post(`/api/v1/firms/${firmId}/license/offline-request`, { key: key || undefined })), null)}>
            {t("license.offline.generate")}
          </Button>
          <a className="rounded-md border border-neutral-300 px-3.5 py-2 text-sm font-medium hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800" href={v.store_links.offline} target="_blank" rel="noreferrer">
            {v.store_links.offline}
          </a>
        </div>
        {offline ? (
          <div className="grid gap-3 md:grid-cols-[1fr_12rem]">
            <div>
              <Field id="reqcode" label={t("license.offline.code")}>
                <Textarea id="reqcode" readOnly value={offline.request_code} className="font-mono text-xs" />
              </Field>
              <Button size="sm" variant="secondary" onClick={() => navigator.clipboard.writeText(offline.request_code)}>
                {t("common.copy")}
              </Button>
              <p className="mt-2 text-xs text-neutral-500">{offline.instructions}</p>
            </div>
            {/* The QR is an SVG the API rendered from the request code (no user content inside) */}
            <div className="h-48 w-48" aria-hidden="true" dangerouslySetInnerHTML={{ __html: offline.qr_svg }} />
          </div>
        ) : null}
        <form
          className="mt-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run("import", () => post(`/api/v1/firms/${firmId}/license/import-file`, { license_file: file }), "license.imported").then(() => setFile(""));
          }}
        >
          <Field id="licfile" label={t("license.import_file")}>
            <Textarea id="licfile" value={file} onChange={(e) => setFile(e.target.value)} className="font-mono text-xs" placeholder="-----BEGIN 21CEL LICENSE-----" />
          </Field>
          <Button type="submit" variant="secondary" disabled={busy || file.trim().length < 20}>
            {t("license.import")}
          </Button>
        </form>
      </Card>

      {v.has_key || v.use === "offline" ? (
        <Card title={t("license.deactivate")}>
          <Button
            variant="danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm(t("license.deactivate.confirm"))) void run("deactivate", () => post(`/api/v1/firms/${firmId}/license/deactivate`), "license.deactivated");
            }}
          >
            {t("license.deactivate")}
          </Button>
        </Card>
      ) : null}
    </div>
  );
}
