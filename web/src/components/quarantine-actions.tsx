"use client";
// Unknown-sender quarantine: release to a client (optionally remembering the sender) or discard.
import { useState } from "react";

import { Button, Notice, Select } from "@/components/ui";
import { ApiError, get, post, type ClientCompany } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

export function QuarantineActions({ documentId, senderIdentity, onDone }: { documentId: string; senderIdentity: string; onDone: () => Promise<void> }) {
  const { t, refreshLicense } = useApp();
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const [clientId, setClientId] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Notice tone="warning" className="mb-3">
      <p className="mb-2 font-medium">{t("quarantine.title", { sender: senderIdentity })}</p>
      {error ? <p className="mb-2 text-sm text-red-700">{error}</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label={t("quarantine.client")} className="w-auto" value={clientId} onChange={(e) => setClientId(e.target.value)}>
          <option value="">{t("quarantine.client")}…</option>
          {(clients.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </Select>
        <label className="flex items-center gap-1 text-sm">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          {t("quarantine.remember")}
        </label>
        <Button size="sm" disabled={busy || !clientId} onClick={() => run(() => post(`/api/v1/documents/${documentId}/release`, { client_company_id: clientId, remember_sender: remember }))}>
          {t("quarantine.release")}
        </Button>
        <Button size="sm" variant="danger" disabled={busy} onClick={() => run(() => post(`/api/v1/documents/${documentId}/discard`))}>
          {t("quarantine.discard")}
        </Button>
      </div>
    </Notice>
  );
}
