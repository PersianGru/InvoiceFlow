"use client";
import { useState } from "react";

import { Badge, Button, Card, Field, Input, Notice, Spinner, confidenceTone } from "@/components/ui";
import { formatPercent } from "@/i18n";
import { ApiError, get, patch, type Supplier } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

export default function SuppliersPage() {
  const { t, locale, user } = useApp();
  const [q, setQ] = useState("");
  const suppliers = useFetch<Supplier[]>(() => get<Supplier[]>("/api/v1/suppliers", { q }), [q]);
  const [selected, setSelected] = useState<Supplier | null>(null);
  const [form, setForm] = useState({ default_gl_account: "", default_cost_category: "", default_vat_code: "" });
  const [flash, setFlash] = useState<string | null>(null);
  const canManage = user?.role === "firm_owner" || user?.role === "accountant";

  function pick(s: Supplier) {
    setSelected(s);
    setForm({ default_gl_account: s.default_gl_account ?? "", default_cost_category: s.default_cost_category ?? "", default_vat_code: s.default_vat_code ?? "" });
    setFlash(null);
  }

  async function save() {
    if (!selected) return;
    try {
      const updated = await patch<Supplier>(`/api/v1/suppliers/${selected.id}`, form);
      setSelected(updated);
      setFlash(t("suppliers.saved"));
      await suppliers.reload();
    } catch (err) {
      setFlash(err instanceof ApiError ? err.message : t("common.error"));
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <div className="mb-3 flex items-center gap-3">
          <h1 className="text-xl font-semibold">{t("suppliers.title")}</h1>
          <label className="sr-only" htmlFor="s-q">
            {t("common.search")}
          </label>
          <Input id="s-q" className="ml-auto w-64" placeholder={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {suppliers.loading && !suppliers.data ? <Spinner label={t("common.loading")} /> : null}
        {suppliers.data ? (
          <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                <tr>
                  <th className="p-2">{t("field.supplier.name")}</th>
                  <th className="p-2">{t("field.supplier.vat_id")}</th>
                  <th className="p-2 text-right">{t("suppliers.documents")}</th>
                  <th className="p-2 text-right">{t("suppliers.corrections")}</th>
                  <th className="p-2">{t("suppliers.accuracy")}</th>
                </tr>
              </thead>
              <tbody>
                {suppliers.data.map((s) => (
                  <tr key={s.id} className="cursor-pointer border-t border-neutral-100 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800" onClick={() => pick(s)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && pick(s)}>
                    <td className="p-2 font-medium">{s.name}</td>
                    <td className="p-2 font-mono text-xs">{s.vat_id ?? t("common.none")}</td>
                    <td className="p-2 text-right tabular-nums">{s.document_count}</td>
                    <td className="p-2 text-right tabular-nums">{s.correction_count}</td>
                    <td className="p-2">{s.accuracy !== null ? <Badge tone={confidenceTone(s.accuracy)}>{formatPercent(locale, s.accuracy)}</Badge> : t("common.none")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
      {selected ? (
        <Card title={selected.name}>
          <dl className="mb-3 text-sm">
            <dt className="text-neutral-600">{t("field.supplier.iban")}</dt>
            <dd className="mb-2 font-mono text-xs">{selected.ibans.join(", ") || t("common.none")}</dd>
            <dt className="text-neutral-600">{t("suppliers.rules")}</dt>
            <dd className="mb-2 font-mono text-xs">{Object.keys(selected.field_rules).length ? Object.entries(selected.field_rules).map(([k, v]) => `${k} = ${v}`).join("; ") : t("common.none")}</dd>
          </dl>
          <h3 className="mb-2 text-sm font-semibold">{t("suppliers.defaults")}</h3>
          {flash ? <Notice className="mb-2">{flash}</Notice> : null}
          <Field id="gl" label={t("review.gl_account")}>
            <Input id="gl" value={form.default_gl_account} readOnly={!canManage} onChange={(e) => setForm({ ...form, default_gl_account: e.target.value })} />
          </Field>
          <Field id="cost" label={t("review.cost_category")}>
            <Input id="cost" value={form.default_cost_category} readOnly={!canManage} onChange={(e) => setForm({ ...form, default_cost_category: e.target.value })} />
          </Field>
          <Field id="vat" label={t("review.vat_code")}>
            <Input id="vat" value={form.default_vat_code} readOnly={!canManage} onChange={(e) => setForm({ ...form, default_vat_code: e.target.value })} />
          </Field>
          {canManage ? <Button onClick={save}>{t("common.save")}</Button> : null}
        </Card>
      ) : null}
    </div>
  );
}
