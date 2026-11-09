"use client";
// Backups for a firm or the platform: run now, list, reveal the password (2FA, max 5),
// download, recovery key (shown once), restore (firm), and the backup target/schedule settings.
import { useState } from "react";

import { useReason } from "@/components/reason-dialog";
import { SettingsSection, type SectionView } from "@/components/settings-section";
import { Badge, Button, Card, Field, Input, Notice, Spinner } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { API_URL, ApiError, get, post } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

interface BackupRow {
  id: string;
  kind: string;
  status: string;
  trigger: string;
  target: string;
  size_bytes: number;
  reveals_left: number;
  recovery_key: boolean;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
  deleted_at: string | null;
}

interface Overview {
  backups: BackupRow[];
  recovery_key: { set: boolean; confirmed: boolean; public_key: string | null };
  password_not_saved: boolean;
  restores: Array<{ id: string; status: string; reason: string; error: string | null; created_at: string | null }>;
}

const TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = { done: "success", running: "warning", queued: "neutral", failed: "danger" };

function size(n: number): string {
  return n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

export function BackupsPanel({ scope, section, onSectionSaved }: { scope: "firm" | "platform"; section?: SectionView; onSectionSaved?: () => Promise<void> }) {
  const { t, locale } = useApp();
  const base = scope === "firm" ? "/api/v1/backups" : "/api/v1/platform/backups";
  const keyBase = scope === "firm" ? "/api/v1/backups/recovery-key" : "/api/v1/platform/recovery-key";
  const data = useFetch<Overview>(() => get<Overview>(base), [base]);
  const [code, setCode] = useState("");
  const [shown, setShown] = useState<{ id: string; password: string; left: number } | null>(null);
  const [privateKey, setPrivateKey] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, ask] = useReason();

  async function run<T>(fn: () => Promise<T>, ok?: string): Promise<T | null> {
    setBusy(true);
    setFlash(null);
    try {
      const r = await fn();
      if (ok) setFlash({ tone: "success", text: ok });
      await data.reload();
      return r;
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function restore(b: BackupRow) {
    const reason = await ask(t("backups.restore"), { body: t("backups.restore_body"), danger: true });
    if (!reason) return;
    await run(() => post(`${base}/${b.id}/restore`, { code, reason }), t("backups.restore_started"));
  }

  if (!data.data) return data.error ? <Notice tone="danger">{data.error}</Notice> : <Spinner label={t("common.loading")} />;
  const o = data.data;
  return (
    <div className="space-y-4">
      {dialog}
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"}>
          {flash.text}
        </Notice>
      ) : null}
      {o.password_not_saved ? (
        <Notice tone="warning" role="alert">
          {t("backups.not_saved")}
        </Notice>
      ) : null}
      <Card
        title={t("backups.title")}
        actions={
          <Button size="sm" disabled={busy} onClick={() => void run(() => post(base), t("backups.started"))}>
            {t("backups.now")}
          </Button>
        }
      >
        <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">{t("backups.intro")}</p>
        <Field id="b-code" label={t("backups.code")} hint={t("backups.code_hint")}>
          <Input id="b-code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} className="w-40" />
        </Field>
        {shown ? (
          <Notice tone="warning" className="mb-3">
            <p className="font-mono text-base break-all">{shown.password}</p>
            <p className="text-xs">{t("backups.password_shown", { n: shown.left })}</p>
            <Button size="sm" variant="secondary" className="mt-1" onClick={() => void navigator.clipboard?.writeText(shown.password)}>
              {t("common.copy")}
            </Button>
          </Notice>
        ) : null}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-neutral-600 dark:text-neutral-300">
              <tr>
                <th className="p-2">{t("backups.when")}</th>
                <th className="p-2">{t("backups.status")}</th>
                <th className="p-2">{t("backups.size")}</th>
                <th className="p-2">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {o.backups.map((b) => (
                <tr key={b.id} className="border-t border-neutral-100 align-top dark:border-neutral-800">
                  <td className="whitespace-nowrap p-2">
                    {formatDateTime(locale, b.finished_at ?? b.created_at)}
                    <div className="text-xs text-neutral-600">
                      {t(`backups.trigger.${b.trigger}` as "backups.trigger.manual")} · {b.target}
                      {b.recovery_key ? ` · ${t("backups.with_key")}` : ""}
                    </div>
                  </td>
                  <td className="p-2">
                    <Badge tone={b.deleted_at ? "neutral" : (TONE[b.status] ?? "neutral")}>{b.deleted_at ? t("backups.pruned") : b.status}</Badge>
                    {b.error ? <div className="text-xs text-red-700">{b.error}</div> : null}
                  </td>
                  <td className="p-2 tabular-nums">{b.size_bytes ? size(b.size_bytes) : "—"}</td>
                  <td className="p-2">
                    {b.status === "done" && !b.deleted_at ? (
                      <div className="flex flex-wrap gap-1">
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={busy || b.reveals_left === 0 || code.length < 6}
                          onClick={async () => {
                            const r = await run(() => post<{ password: string; reveals_left: number }>(`${base}/${b.id}/reveal`, { code }));
                            if (r) setShown({ id: b.id, password: r.password, left: r.reveals_left });
                          }}
                        >
                          {t("backups.reveal", { n: b.reveals_left })}
                        </Button>
                        <a className="rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800" href={`${API_URL}${base}/${b.id}/download`}>
                          {t("backups.download")}
                        </a>
                        {scope === "firm" && b.kind === "firm" ? (
                          <Button size="sm" variant="danger" disabled={busy || code.length < 6} onClick={() => void restore(b)}>
                            {t("backups.restore")}
                          </Button>
                        ) : null}
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
              {o.backups.length === 0 ? (
                <tr>
                  <td colSpan={4} className="p-4 text-center text-neutral-600">
                    {t("backups.none")}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={t("backups.recovery_title")}>
        <p className="mb-2 text-sm text-neutral-600 dark:text-neutral-400">{t("backups.recovery_intro")}</p>
        <p className="mb-2 text-sm">
          {o.recovery_key.set ? (
            <Badge tone={o.recovery_key.confirmed ? "success" : "warning"}>{o.recovery_key.confirmed ? t("backups.key_confirmed") : t("backups.key_unconfirmed")}</Badge>
          ) : (
            <Badge tone="warning">{t("backups.key_none")}</Badge>
          )}
        </p>
        {privateKey ? (
          <Notice tone="warning" className="mb-2">
            <p className="break-all font-mono text-xs">{privateKey}</p>
            <div className="mt-2 flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => void navigator.clipboard?.writeText(privateKey)}>
                {t("common.copy")}
              </Button>
              <Button
                size="sm"
                onClick={async () => {
                  await run(() => post(`${keyBase}/confirm`), t("backups.key_confirmed"));
                  setPrivateKey(null);
                }}
              >
                {t("backups.key_saved")}
              </Button>
            </div>
          </Notice>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={async () => {
              const r = await run(() => post<{ private_key: string }>(keyBase));
              if (r) setPrivateKey(r.private_key);
            }}
          >
            {o.recovery_key.set ? t("backups.key_new") : t("backups.key_create")}
          </Button>
        )}
      </Card>
      {o.restores.length ? (
        <Card title={t("backups.restores")}>
          <ul className="space-y-1 text-sm">
            {o.restores.map((r) => (
              <li key={r.id}>
                <Badge tone={TONE[r.status] ?? "neutral"}>{r.status}</Badge> {formatDateTime(locale, r.created_at)} · “{r.reason}” {r.error ? <span className="text-red-700">{r.error}</span> : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
      {section && onSectionSaved ? (
        <SettingsSection view={section} title={t("backups.settings")} intro={t("backups.settings_intro")} path={scope === "firm" ? "/api/v1/firm-settings/backup" : "/api/v1/platform/settings/backup"} onSaved={onSectionSaved} />
      ) : null}
    </div>
  );
}
