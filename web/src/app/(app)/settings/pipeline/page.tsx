"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Select, Spinner } from "@/components/ui";
import { ApiError, get, patch, post, type EngineCatalogue, type EngineTestResult, type FirmSettings } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

export default function PipelineSettingsPage() {
  const { t, user } = useApp();
  const firmId = user?.firm_id;
  const settings = useFetch<FirmSettings>(() => get<FirmSettings>(`/api/v1/firms/${firmId}/settings`), [firmId]);
  if (!settings.data || !firmId) return <Spinner label={t("common.loading")} />;
  // keyed on the loaded values: a reload re-mounts the form with fresh initial state
  return (
    <div className="space-y-4">
      <SettingsForm key={JSON.stringify(settings.data)} firmId={firmId} initial={settings.data} />
      <EnginesCard />
    </div>
  );
}

function EnginesCard() {
  const { t } = useApp();
  const engines = useFetch<EngineCatalogue>(() => get<EngineCatalogue>("/api/v1/engines"), []);
  const [result, setResult] = useState<EngineTestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const e = engines.data;
  const tierLabel = (key: string) => t(`engines.key.${key}` as "engines.key.tesseract", {}) || key;
  return (
    <Card
      title={t("engines.title")}
      actions={
        <Button
          size="sm"
          variant="secondary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              setResult(await post<EngineTestResult>("/api/v1/engines/test"));
            } catch (err) {
              setError(err instanceof ApiError ? err.message : t("common.error"));
            } finally {
              setBusy(false);
            }
          }}
        >
          {t("engines.test")}
        </Button>
      }
    >
      <p className="mb-3 text-sm text-neutral-600">{t("engines.help")}</p>
      {error ? (
        <Notice tone="danger" className="mb-3">
          {error}
        </Notice>
      ) : null}
      {!e ? <Spinner label={t("common.loading")} /> : null}
      {e ? (
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[14rem_1fr]">
          <dt className="text-neutral-600">{t("engines.tier1")}</dt>
          <dd>
            {t("engines.tier1.value")} {e.renderer ? <Badge tone="success">{t("engines.renderer")}</Badge> : <Badge tone="warning">{t("engines.no_renderer")}</Badge>}
          </dd>
          <dt className="text-neutral-600">{t("engines.local_ocr")}</dt>
          <dd className="flex flex-wrap gap-1">
            {e.local_ocr.length === 0 ? <Badge tone="neutral">{t("engines.none")}</Badge> : null}
            {e.local_ocr.map((row) => (
              <Badge key={row.key} tone={row.active ? "success" : row.available ? "info" : "neutral"} title={row.available ? "" : t("engines.not_installed")}>
                {tierLabel(row.key)}
                {row.active ? ` · ${t("engines.active")}` : row.available ? "" : ` · ${t("engines.not_installed")}`}
              </Badge>
            ))}
            {result?.local_ocr ? <span className="text-xs text-neutral-600">{result.local_ocr.ok ? t("engines.test.ocr_ok", { ms: result.local_ocr.ms ?? 0, words: result.local_ocr.words ?? 0 }) : t("engines.test.failed", { e: result.local_ocr.error ?? "" })}</span> : null}
          </dd>
          <dt className="text-neutral-600">{t("engines.local_llm")}</dt>
          <dd>
            {e.local_llm ? (
              <>
                <Badge tone="success">{tierLabel(e.local_llm.key)}</Badge> <span className="font-mono text-xs">{e.local_llm.model ?? ""}</span>
              </>
            ) : (
              <Badge tone="neutral">{t("engines.none")}</Badge>
            )}
            {result?.local_llm ? <span className="ml-2 text-xs text-neutral-600">{result.local_llm.ok ? t("engines.test.ok", { ms: result.local_llm.ms ?? 0 }) : t("engines.test.failed", { e: result.local_llm.error ?? t("engines.unavailable") })}</span> : null}
          </dd>
          <dt className="text-neutral-600">{t("engines.builtin_model")}</dt>
          <dd>
            <span className="font-mono text-xs">{e.builtin_model.file}</span> {e.builtin_model.ready ? <Badge tone="success">{t("engines.model_ready")}</Badge> : <Badge tone="warning">{e.builtin_model.auto_download ? t("engines.model_downloading") : t("engines.model_missing")}</Badge>}
            <span className="block text-xs text-neutral-600">
              {e.builtin_model.size_mb ? `${e.builtin_model.size_mb} MB · ` : ""}
              {e.builtin_model.ram_hint ?? ""}
            </span>
          </dd>
          <dt className="text-neutral-600">{t("engines.cloud_ocr")}</dt>
          <dd>{e.cloud_ocr.length ? e.cloud_ocr.map((row) => <Badge key={row.key} tone="info">{tierLabel(row.key)}</Badge>) : <Badge tone="neutral">{t("engines.none")}</Badge>}</dd>
          <dt className="text-neutral-600">{t("engines.cloud_llm")}</dt>
          <dd>
            {e.cloud_llm ? (
              <>
                <Badge tone="info">{tierLabel(e.cloud_llm.key)}</Badge> <span className="font-mono text-xs">{e.cloud_llm.model ?? ""}</span>
                {e.cloud_llm.vision ? <Badge tone="neutral" className="ml-1">{t("engines.vision")}</Badge> : null}
              </>
            ) : (
              <Badge tone="neutral">{t("engines.none")}</Badge>
            )}
            {result?.cloud_llm ? <span className="ml-2 text-xs text-neutral-600">{result.cloud_llm.ok ? t("engines.test.ok", { ms: result.cloud_llm.ms ?? 0 }) : t("engines.test.failed", { e: result.cloud_llm.error ?? t("engines.unavailable") })}</span> : null}
          </dd>
          <dt className="text-neutral-600">{t("engines.languages")}</dt>
          <dd className="font-mono text-xs">{e.settings.ocr_languages}</dd>
        </dl>
      ) : null}
      <p className="mt-3 text-xs text-neutral-600">{t("engines.footer")}</p>
    </Card>
  );
}

function SettingsForm({ firmId, initial }: { firmId: string; initial: FirmSettings }) {
  const { t, license } = useApp();
  const [form, setForm] = useState<FirmSettings>(initial);
  const [flash, setFlash] = useState<string | null>(null);
  const cloudEntitled = license?.features?.cloud_ai === true;

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      setForm(await patch<FirmSettings>(`/api/v1/firms/${firmId}/settings`, form));
      setFlash(t("settings.pipeline.saved"));
    } catch (err) {
      setFlash(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  return (
    <Card title={t("settings.pipeline.title")}>
      {flash ? <Notice className="mb-3">{flash}</Notice> : null}
      <form onSubmit={submit} className="max-w-lg">
        <Field id="threshold" label={t("settings.pipeline.threshold")}>
          <Input id="threshold" type="number" min={0.5} max={1} step={0.01} value={form.confidence_threshold} onChange={(e) => setForm({ ...form, confidence_threshold: Number(e.target.value) })} />
        </Field>
        <label className="mb-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.auto_approve} onChange={(e) => setForm({ ...form, auto_approve: e.target.checked })} /> {t("settings.pipeline.auto_approve")}
        </label>
        <Field id="policy" label={t("settings.pipeline.policy")}>
          <Select id="policy" value={form.ai_routing_policy} onChange={(e) => setForm({ ...form, ai_routing_policy: e.target.value })}>
            {(["local_only", "local_first_cloud_fallback", "cloud_first"] as const).map((p) => (
              <option key={p} value={p}>
                {t(`settings.pipeline.policy.${p}`)}
              </option>
            ))}
          </Select>
        </Field>
        <label className="mb-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.cloud_ai_opt_in} disabled={!cloudEntitled && !form.cloud_ai_opt_in} onChange={(e) => setForm({ ...form, cloud_ai_opt_in: e.target.checked })} /> {t("settings.pipeline.cloud_opt_in")}
        </label>
        <Field id="retention" label={t("settings.pipeline.retention")}>
          <Input id="retention" type="number" min={365} max={36500} value={form.retention_days} onChange={(e) => setForm({ ...form, retention_days: Number(e.target.value) })} />
        </Field>
        <Field id="cloud-cap" label={t("settings.pipeline.cloud_cap")} hint={t("settings.pipeline.cloud_cap.hint")}>
          <Input id="cloud-cap" type="number" min={0} value={form.cloud_ai_monthly_cap ?? 0} onChange={(e) => setForm({ ...form, cloud_ai_monthly_cap: Number(e.target.value) || 0 })} />
        </Field>
        <Field id="currency" label={t("settings.pipeline.base_currency")}>
          <Input id="currency" maxLength={3} value={form.base_currency} onChange={(e) => setForm({ ...form, base_currency: e.target.value.toUpperCase() })} />
        </Field>
        <Button type="submit">{t("common.save")}</Button>
      </form>
    </Card>
  );
}
