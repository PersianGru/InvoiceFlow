"use client";
// The platform license: only the number of firms. Issued by 21CEL on request; without it
// the platform runs one firm. Same flows as a firm license (online key, offline file), never the key.
import { useState } from "react";

import { Badge, Button, Card, Field, Input, Notice, Spinner, Textarea } from "@/components/ui";
import { formatDate } from "@/i18n";
import { ApiError, get, post, type LicenseView, type PlatformOverview } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

export default function PlatformLicensePage() {
  const { t, locale } = useApp();
  const ov = useFetch<PlatformOverview>(() => get<PlatformOverview>("/api/v1/platform/overview"), []);
  const pid = ov.data?.platform_firm_id ?? null;
  const lic = useFetch<LicenseView | null>(() => (pid ? get<LicenseView>(`/api/v1/firms/${pid}/license`) : Promise.resolve(null)), [pid]);
  const [key, setKey] = useState("");
  const [file, setFile] = useState("");
  const [offline, setOffline] = useState<{ request_code: string; qr_svg: string; instructions: string } | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  if (!pid || !lic.data) return <Spinner label={t("common.loading")} />;
  const v = lic.data;

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setFlash(null);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "request_code" in (r as object)) setOffline(r as typeof offline);
      await lic.reload();
      await ov.reload();
      setFlash({ tone: "success", text: ok });
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    } finally {
      setBusy(false);
    }
  }

  const base = `/api/v1/firms/${pid}/license`;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{t("platform.license")}</h1>
      <Notice>{t("platform.license_intro")}</Notice>
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"}>
          {flash.text}
        </Notice>
      ) : null}
      <Card
        title={
          <span className="flex items-center gap-2">
            {t("platform.license")} <Badge tone={v.state === "valid" ? "success" : v.state === "grace" ? "warning" : "neutral"}>{v.state}</Badge>
          </span>
        }
        actions={
          v.has_key ? (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => void run(() => post(`${base}/refresh`), t("license.checked"))}>
              {t("license.refresh")}
            </Button>
          ) : null
        }
      >
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <dt>{t("platform.max_firms")}</dt>
          <dd>{ov.data?.max_firms && ov.data.max_firms >= 1000000 ? "∞" : ov.data?.max_firms}</dd>
          <dt>{t("license.installation_id")}</dt>
          <dd className="font-mono text-xs">{v.installation_id_grouped}</dd>
          <dt>{t("license.key_hint")}</dt>
          <dd>{v.kh ? `…${v.kh}` : "—"}</dd>
          <dt>{t("license.expires")}</dt>
          <dd>{v.expires_at ? formatDate(locale, v.expires_at) : "—"}</dd>
        </dl>
        {v.has_key && v.use !== "offline" ? (
          <Button className="mt-3" size="sm" variant="danger" disabled={busy} onClick={() => void run(() => post(`${base}/deactivate`), t("license.deactivated"))}>
            {t("license.deactivate")}
          </Button>
        ) : null}
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <Card title={t("license.enter_key")}>
          <Field id="pkey" label={t("license.key")}>
            <Input id="pkey" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
          </Field>
          <Button disabled={busy || !key} onClick={() => void run(() => post(`${base}/activate`, { key }), t("license.activated"))}>
            {t("license.activate")}
          </Button>
        </Card>
        <Card title={t("license.offline")}>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => void run(() => post(`${base}/offline-request`, { key: key || null }), t("common.saved"))}>
            {t("license.offline_request")}
          </Button>
          {offline ? (
            <div className="mt-2 text-sm">
              <code className="block break-all rounded bg-neutral-100 px-2 py-1 dark:bg-neutral-800">{offline.request_code}</code>
              <p className="mt-1">{offline.instructions}</p>
            </div>
          ) : null}
          <Field id="pfile" label={t("license.import_file")}>
            <Textarea id="pfile" rows={3} value={file} onChange={(e) => setFile(e.target.value)} />
          </Field>
          <Button size="sm" disabled={busy || !file} onClick={() => void run(() => post(`${base}/import-file`, { license_file: file }), t("license.imported"))}>
            {t("license.import")}
          </Button>
        </Card>
      </div>
    </div>
  );
}
