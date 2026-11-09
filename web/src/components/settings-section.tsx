"use client";
// One dashboard settings section (SMTP, IMAP, AI, backup target …) rendered from the API's field list.
// Secrets are write-only: the form shows "set" / "not set" and an empty field keeps the stored value.
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice } from "@/components/ui";
import { ApiError, put } from "@/lib/api";
import { useApp } from "@/lib/app-context";

export interface SectionField {
  name: string;
  kind: "str" | "int" | "bool" | "secret";
}

export interface SectionView {
  key: string;
  own: Record<string, unknown> | null;
  own_enabled: boolean;
  effective_source: "firm" | "platform" | "none";
  effective: Record<string, unknown>;
  fields: SectionField[];
  source?: string | null;
}

type Values = Record<string, string | boolean>;

function initial(view: SectionView): Values {
  const out: Values = {};
  for (const f of view.fields) {
    const v = view.own?.[f.name];
    if (f.kind === "secret") out[f.name] = "";
    else if (f.kind === "bool") out[f.name] = v === undefined || v === null ? Boolean(view.effective[f.name]) : Boolean(v);
    else out[f.name] = v === undefined || v === null ? "" : String(v);
  }
  return out;
}

export function SettingsSection({
  view,
  title,
  intro,
  path,
  onSaved,
  extra,
  labels,
}: {
  view: SectionView;
  title: string;
  intro?: string;
  path: string;
  onSaved: () => Promise<void> | void;
  extra?: React.ReactNode;
  labels?: Record<string, string>;
}) {
  const { t } = useApp();
  const [values, setValues] = useState<Values>(() => initial(view));
  const [clear, setClear] = useState<string[]>([]);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFlash(null);
    const payload: Record<string, unknown> = {};
    for (const f of view.fields) {
      const v = values[f.name];
      if (f.kind === "secret") {
        if (typeof v === "string" && v !== "") payload[f.name] = v;
      } else if (f.kind === "int") {
        if (v !== "") payload[f.name] = Number(v);
      } else payload[f.name] = v;
    }
    try {
      await put(path, { values: payload, enabled: true, clear_secrets: clear });
      setFlash({ tone: "success", text: t("common.saved") });
      setClear([]);
      await onSaved();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    } finally {
      setBusy(false);
    }
  }

  const sourceBadge =
    view.effective_source === "firm" ? t("settings.source.firm") : view.effective_source === "platform" ? t("settings.source.platform") : t("settings.source.none");

  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          {title} <Badge tone={view.effective_source === "none" ? "warning" : "success"}>{sourceBadge}</Badge>
          {view.source === "environment" ? <Badge tone="info">{t("settings.from_env")}</Badge> : null}
        </span>
      }
    >
      {intro ? <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">{intro}</p> : null}
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      <form onSubmit={save} className="grid gap-x-4 md:grid-cols-2">
        {view.fields.map((f) => {
          const id = `${view.key}-${f.name}`;
          const label = labels?.[f.name] ?? f.name.replaceAll("_", " ");
          if (f.kind === "bool") {
            return (
              <label key={f.name} className="mb-3 flex items-center gap-2 text-sm">
                <input type="checkbox" checked={Boolean(values[f.name])} onChange={(e) => setValues({ ...values, [f.name]: e.target.checked })} />
                {label}
              </label>
            );
          }
          const isSet = f.kind === "secret" && (view.own?.[f.name] as { set?: boolean } | undefined)?.set === true;
          return (
            <Field
              key={f.name}
              id={id}
              label={label}
              hint={
                f.kind === "secret" ? (
                  <span className="flex items-center gap-2">
                    {isSet && !clear.includes(f.name) ? t("settings.secret_set") : t("settings.secret_not_set")}
                    {isSet ? (
                      <button type="button" className="underline" onClick={() => setClear(clear.includes(f.name) ? clear.filter((c) => c !== f.name) : [...clear, f.name])}>
                        {clear.includes(f.name) ? t("common.cancel") : t("settings.secret_clear")}
                      </button>
                    ) : null}
                  </span>
                ) : view.effective_source === "platform" && view.effective[f.name] !== undefined && view.effective[f.name] !== null ? (
                  t("settings.platform_value", { value: String(view.effective[f.name]) })
                ) : undefined
              }
            >
              <Input
                id={id}
                type={f.kind === "secret" ? "password" : f.kind === "int" ? "number" : "text"}
                autoComplete={f.kind === "secret" ? "new-password" : "off"}
                value={String(values[f.name] ?? "")}
                onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
              />
            </Field>
          );
        })}
        <div className="flex flex-wrap gap-2 md:col-span-2">
          <Button type="submit" disabled={busy}>
            {t("common.save")}
          </Button>
          {extra}
        </div>
      </form>
    </Card>
  );
}
