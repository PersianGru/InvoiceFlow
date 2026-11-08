"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Select } from "@/components/ui";
import { ApiError, del, get, patch, post, type ClientCompany, type ExportColumnsInfo, type ExportTemplate } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { TEMPLATE_FORMATS, addColumn, moveColumn, removeColumn, type TemplateColumn } from "@/lib/integrations";
import { useFetch } from "@/lib/use-fetch";

interface Draft {
  id: string | null;
  name: string;
  format: "csv" | "xlsx" | "datev";
  client_company_id: string;
  columns: TemplateColumn[];
  decimal_separator: string;
  date_format: string;
  delimiter: string;
  per_line: boolean;
  datev_consultant_number: string;
  datev_client_number: string;
}

function emptyDraft(defaults: TemplateColumn[]): Draft {
  return { id: null, name: "", format: "csv", client_company_id: "", columns: defaults, decimal_separator: ".", date_format: "%Y-%m-%d", delimiter: "", per_line: false, datev_consultant_number: "", datev_client_number: "" };
}

function toDraft(tp: ExportTemplate): Draft {
  const o = tp.options as Record<string, string | boolean | undefined>;
  return {
    id: tp.id,
    name: tp.name,
    format: tp.format,
    client_company_id: tp.client_company_id ?? "",
    columns: tp.columns,
    decimal_separator: String(o.decimal_separator ?? "."),
    date_format: String(o.date_format ?? "%Y-%m-%d"),
    delimiter: String(o.delimiter ?? ""),
    per_line: Boolean(o.per_line),
    datev_consultant_number: String(o.datev_consultant_number ?? ""),
    datev_client_number: String(o.datev_client_number ?? ""),
  };
}

export default function ExportTemplatesPage() {
  const { t } = useApp();
  const templates = useFetch<ExportTemplate[]>(() => get<ExportTemplate[]>("/api/v1/export-templates"), []);
  const registry = useFetch<ExportColumnsInfo>(() => get<ExportColumnsInfo>("/api/v1/exports/columns"), []);
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [addKey, setAddKey] = useState("");
  const labels = new Map((registry.data?.columns ?? []).map((c) => [c.key, c.label]));
  const clientName = new Map((clients.data ?? []).map((c) => [c.id, c.name]));

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!draft) return;
    const options: Record<string, unknown> = { decimal_separator: draft.decimal_separator, date_format: draft.date_format, per_line: draft.per_line };
    if (draft.delimiter) options.delimiter = draft.delimiter;
    if (draft.format === "datev") {
      options.datev_consultant_number = draft.datev_consultant_number;
      options.datev_client_number = draft.datev_client_number;
    }
    const body = { name: draft.name, format: draft.format, client_company_id: draft.client_company_id || null, columns: draft.columns, options };
    try {
      if (draft.id) await patch(`/api/v1/export-templates/${draft.id}`, body);
      else await post("/api/v1/export-templates", body);
      setFlash({ tone: "success", text: t("templates.saved") });
      setDraft(null);
      await templates.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    }
  }

  async function remove(id: string) {
    if (!window.confirm(t("common.confirm_delete"))) return;
    try {
      await del(`/api/v1/export-templates/${id}`);
      setFlash({ tone: "success", text: t("templates.deleted") });
      await templates.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
    }
  }

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">{t("templates.title")}</h1>
      <p className="mb-3 text-sm text-neutral-600">{t("templates.help")}</p>
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      {!draft ? (
        <div className="mb-3">
          <Button onClick={() => setDraft(emptyDraft(registry.data?.default ?? []))}>{t("templates.new")}</Button>
        </div>
      ) : null}
      {draft ? (
        <Card title={draft.id ? t("templates.edit") : t("templates.new")} className="mb-4">
          <form onSubmit={save}>
            <div className="grid gap-3 md:grid-cols-3">
              <Field id="tp-name" label={t("common.name")}>
                <Input id="tp-name" required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              </Field>
              <Field id="tp-format" label={t("common.format")}>
                <Select id="tp-format" value={draft.format} onChange={(e) => setDraft({ ...draft, format: e.target.value as Draft["format"] })}>
                  {TEMPLATE_FORMATS.map((f) => (
                    <option key={f} value={f}>
                      {t(`exports.format.${f}` as "exports.format.csv")}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field id="tp-client" label={t("templates.client")}>
                <Select id="tp-client" value={draft.client_company_id} onChange={(e) => setDraft({ ...draft, client_company_id: e.target.value })}>
                  <option value="">{t("templates.client.any")}</option>
                  {(clients.data ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
              {draft.format !== "datev" ? (
                <>
                  <Field id="tp-dec" label={t("templates.decimal")}>
                    <Select id="tp-dec" value={draft.decimal_separator} onChange={(e) => setDraft({ ...draft, decimal_separator: e.target.value })}>
                      <option value=".">.</option>
                      <option value=",">,</option>
                    </Select>
                  </Field>
                  <Field id="tp-date" label={t("templates.date_format")}>
                    <Select id="tp-date" value={draft.date_format} onChange={(e) => setDraft({ ...draft, date_format: e.target.value })}>
                      <option value="%Y-%m-%d">2026-12-31</option>
                      <option value="%d.%m.%Y">31.12.2026</option>
                      <option value="%d/%m/%Y">31/12/2026</option>
                    </Select>
                  </Field>
                  {draft.format === "csv" ? (
                    <Field id="tp-delim" label={t("templates.delimiter")}>
                      <Select id="tp-delim" value={draft.delimiter} onChange={(e) => setDraft({ ...draft, delimiter: e.target.value })}>
                        <option value="">auto</option>
                        <option value=";">;</option>
                        <option value=",">,</option>
                        <option value="\t">tab</option>
                      </Select>
                    </Field>
                  ) : null}
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={draft.per_line} onChange={(e) => setDraft({ ...draft, per_line: e.target.checked })} />
                    {t("templates.per_line")}
                  </label>
                </>
              ) : (
                <>
                  <Field id="tp-dc" label={t("exports.datev.consultant")}>
                    <Input id="tp-dc" inputMode="numeric" value={draft.datev_consultant_number} onChange={(e) => setDraft({ ...draft, datev_consultant_number: e.target.value })} />
                  </Field>
                  <Field id="tp-dm" label={t("exports.datev.client")}>
                    <Input id="tp-dm" inputMode="numeric" value={draft.datev_client_number} onChange={(e) => setDraft({ ...draft, datev_client_number: e.target.value })} />
                  </Field>
                </>
              )}
            </div>
            {draft.format !== "datev" ? (
              <fieldset className="mt-3">
                <legend className="mb-1 text-sm font-medium">{t("templates.columns")}</legend>
                <ol className="divide-y divide-neutral-100 rounded-md border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
                  {draft.columns.map((c, i) => (
                    <li key={c.key} className="flex flex-wrap items-center gap-2 p-2 text-sm">
                      <span className="w-48 truncate font-mono text-xs text-neutral-500">{c.key}</span>
                      <Input aria-label={t("templates.header")} className="w-56" value={c.header} onChange={(e) => setDraft({ ...draft, columns: draft.columns.map((x) => (x.key === c.key ? { ...x, header: e.target.value } : x)) })} />
                      <Button size="sm" variant="secondary" aria-label={t("templates.move_up")} disabled={i === 0} onClick={() => setDraft({ ...draft, columns: moveColumn(draft.columns, i, -1) })}>
                        ↑
                      </Button>
                      <Button size="sm" variant="secondary" aria-label={t("templates.move_down")} disabled={i === draft.columns.length - 1} onClick={() => setDraft({ ...draft, columns: moveColumn(draft.columns, i, 1) })}>
                        ↓
                      </Button>
                      <Button size="sm" variant="danger" onClick={() => setDraft({ ...draft, columns: removeColumn(draft.columns, c.key) })}>
                        {t("templates.remove")}
                      </Button>
                    </li>
                  ))}
                </ol>
                <div className="mt-2 flex items-center gap-2">
                  <Select aria-label={t("templates.add_column")} className="w-auto" value={addKey} onChange={(e) => setAddKey(e.target.value)}>
                    <option value="">{t("templates.add_column")}</option>
                    {(registry.data?.columns ?? [])
                      .filter((c) => !draft.columns.some((x) => x.key === c.key))
                      .map((c) => (
                        <option key={c.key} value={c.key}>
                          {c.group} · {c.label}
                        </option>
                      ))}
                  </Select>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={!addKey}
                    onClick={() => {
                      setDraft({ ...draft, columns: addColumn(draft.columns, addKey, labels.get(addKey) ?? addKey) });
                      setAddKey("");
                    }}
                  >
                    {t("templates.add_column")}
                  </Button>
                </div>
              </fieldset>
            ) : null}
            <div className="mt-3 flex gap-2">
              <Button type="submit">{t("common.save")}</Button>
              <Button variant="secondary" onClick={() => setDraft(null)}>
                {t("common.cancel")}
              </Button>
            </div>
          </form>
        </Card>
      ) : null}
      <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
            <tr>
              <th className="p-2">{t("common.name")}</th>
              <th className="p-2">{t("common.format")}</th>
              <th className="p-2">{t("templates.client")}</th>
              <th className="p-2">{t("templates.columns")}</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {(templates.data ?? []).map((tp) => (
              <tr key={tp.id} className="border-t border-neutral-100 dark:border-neutral-800">
                <td className="p-2 font-medium">{tp.name}</td>
                <td className="p-2">
                  <Badge>{tp.format}</Badge>
                </td>
                <td className="p-2">{tp.client_company_id ? (clientName.get(tp.client_company_id) ?? "…") : t("templates.client.any")}</td>
                <td className="p-2 text-neutral-600">{tp.format === "datev" ? "—" : tp.columns.map((c) => c.header).join(", ")}</td>
                <td className="p-2 text-right">
                  <Button size="sm" variant="secondary" onClick={() => setDraft(toDraft(tp))}>
                    {t("templates.edit")}
                  </Button>{" "}
                  <Button size="sm" variant="danger" onClick={() => remove(tp.id)}>
                    {t("common.delete")}
                  </Button>
                </td>
              </tr>
            ))}
            {templates.data && templates.data.length === 0 ? (
              <tr>
                <td className="p-6 text-center text-neutral-600" colSpan={5}>
                  {t("templates.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
