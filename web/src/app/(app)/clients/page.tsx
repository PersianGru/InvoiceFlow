"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Select, Spinner } from "@/components/ui";
import { ApiError, API_URL, get, patch, post, type ClientCompany } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { ClientSecurity } from "@/components/client-security";
import { useFetch } from "@/lib/use-fetch";

// Every EU member state (plus CH); the Baltic states first, then the rest alphabetically. Each has a
// VAT-rate entry, a retention period and (where one exists) an e-invoicing mandate in the rule packs.
const COUNTRIES = ["EE", "LV", "LT", "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LU", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"];

// Localised country name from the browser (Intl), falling back to the code.
function countryLabel(code: string, locale: string): string {
  try {
    const name = new Intl.DisplayNames([locale], { type: "region" }).of(code);
    return name && name !== code ? `${code} · ${name}` : code;
  } catch {
    return code;
  }
}

export default function ClientsPage() {
  const { t, user, license, refreshLicense, locale } = useApp();
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const [name, setName] = useState("");
  const [country, setCountry] = useState("DE");
  const [peppolId, setPeppolId] = useState("");
  const [taxNumber, setTaxNumber] = useState("");
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const canManage = user?.role === "firm_owner" || user?.role === "accountant" || user?.role === "super_admin";

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await post("/api/v1/client-companies", { name, country, peppol_id: peppolId || null, tax_number: taxNumber || null });
      setName("");
      setPeppolId("");
      setTaxNumber("");
      setFlash({ tone: "success", text: t("clients.created") });
      await clients.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <h1 className="mb-3 text-xl font-semibold">{t("clients.title")}</h1>
        {clients.loading && !clients.data ? <Spinner label={t("common.loading")} /> : null}
        {clients.data ? (
          <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                <tr>
                  <th className="p-2">{t("clients.name")}</th>
                  <th className="p-2">{t("clients.country")}</th>
                  <th className="p-2">{t("clients.inbound")}</th>
                  <th className="p-2">{t("clients.peppol_id")}</th>
                  <th className="p-2">{t("clients.tax_number")}</th>
                  {canManage ? <th className="p-2">{t("clients.accept_any")}</th> : null}
                  {canManage ? <th className="p-2">{t("clients.dsr")}</th> : null}
                </tr>
              </thead>
              <tbody>
                {clients.data.map((c) => (
                  <tr key={c.id} className="border-t border-neutral-100 dark:border-neutral-800">
                    <td className="p-2 font-medium">{c.name}</td>
                    <td className="p-2">{c.country}</td>
                    <td className="p-2 font-mono text-xs">
                      {c.inbound_email_address ?? c.inbound_email_local_part ?? t("common.none")}
                      {Object.values(c.intake_paused ?? {}).some(Boolean) ? (
                        <span className="ml-1">
                          <Badge tone="danger">{t("clients.paused")}</Badge>
                        </span>
                      ) : null}
                      {canManage ? (
                        <button type="button" className="ml-1 font-sans underline" onClick={() => setOpen(open === c.id ? null : c.id)}>
                          {t("clients.security")}
                        </button>
                      ) : null}
                      {open === c.id ? (
                        <div className="mt-2 font-sans">
                          <ClientSecurity client={c} onChanged={clients.reload} onError={(text) => setFlash({ tone: "danger", text })} />
                        </div>
                      ) : null}
                    </td>
                    {(["peppol_id", "tax_number"] as const).map((field) => (
                      <td key={field} className="p-2">
                        {canManage ? (
                          <input
                            aria-label={t(field === "peppol_id" ? "clients.peppol_id" : "clients.tax_number")}
                            className="w-36 rounded border border-neutral-300 px-1 py-0.5 font-mono text-xs dark:border-neutral-700 dark:bg-neutral-900"
                            defaultValue={c[field] ?? ""}
                            placeholder={field === "peppol_id" ? "0208:0123456789" : "NIP / SIREN"}
                            onBlur={async (e) => {
                              if ((e.target.value || null) === (c[field] ?? null)) return;
                              try {
                                await patch(`/api/v1/client-companies/${c.id}`, { [field]: e.target.value });
                                await clients.reload();
                              } catch (err) {
                                setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
                              }
                            }}
                          />
                        ) : (
                          <span className="font-mono text-xs">{c[field] ?? t("common.none")}</span>
                        )}
                      </td>
                    ))}
                    {canManage ? (
                      <td className="p-2">
                        <input
                          type="checkbox"
                          aria-label={t("clients.accept_any")}
                          title={t("clients.accept_any.hint")}
                          checked={c.email_accept_any_sender ?? false}
                          onChange={async (e) => {
                            try {
                              await patch(`/api/v1/client-companies/${c.id}`, { email_accept_any_sender: e.target.checked });
                              await clients.reload();
                            } catch (err) {
                              setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
                            }
                          }}
                        />
                      </td>
                    ) : null}
                    {canManage ? (
                      <td className="space-x-1 whitespace-nowrap p-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={async () => {
                            const res = await fetch(`${API_URL}/api/v1/dsr/export`, { method: "POST", credentials: "include", headers: { "content-type": "application/json" }, body: JSON.stringify({ client_company_id: c.id }) });
                            if (!res.ok) {
                              setFlash({ tone: "danger", text: t("common.error") });
                              return;
                            }
                            const url = URL.createObjectURL(await res.blob());
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = `dsr-export-${c.id}.zip`;
                            a.click();
                            URL.revokeObjectURL(url);
                          }}
                        >
                          {t("clients.dsr.export")}
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={async () => {
                            try {
                              const r = await post<{ restricted: number }>("/api/v1/dsr/restrict", { client_company_id: c.id });
                              setFlash({ tone: "success", text: t("clients.dsr.restricted", { n: r.restricted }) });
                            } catch (err) {
                              setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
                            }
                          }}
                        >
                          {t("clients.dsr.restrict")}
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={async () => {
                            if (!window.confirm(t("clients.dsr.erase.confirm"))) return;
                            try {
                              const r = await post<{ deleted_now: number; scheduled: number }>("/api/v1/dsr/erase", { client_company_id: c.id });
                              setFlash({ tone: "success", text: t("clients.dsr.erased", { d: r.deleted_now, s: r.scheduled }) });
                              await clients.reload();
                            } catch (err) {
                              setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
                            }
                          }}
                        >
                          {t("clients.dsr.erase")}
                        </Button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
      {canManage ? (
        <Card title={t("clients.new")}>
          {flash ? (
            <Notice tone={flash.tone} className="mb-3">
              {flash.text}
            </Notice>
          ) : null}
          <form onSubmit={submit}>
            <Field id="c-name" label={t("clients.name")}>
              <Input id="c-name" required value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field id="c-country" label={t("clients.country")}>
              <Select id="c-country" value={country} onChange={(e) => setCountry(e.target.value)}>
                {COUNTRIES.map((c) => (
                  <option key={c} value={c}>
                    {countryLabel(c, locale)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field id="c-peppol" label={t("clients.peppol_id")} hint={t("clients.peppol_id.hint")}>
              <Input id="c-peppol" value={peppolId} onChange={(e) => setPeppolId(e.target.value)} placeholder="0208:0123456789" />
            </Field>
            <Field id="c-tax" label={t("clients.tax_number")} hint={t("clients.tax_number.hint")}>
              <Input id="c-tax" value={taxNumber} onChange={(e) => setTaxNumber(e.target.value)} />
            </Field>
            <Button type="submit" disabled={license !== null && !license.core_on}>
              {t("common.save")}
            </Button>
          </form>
        </Card>
      ) : null}
    </div>
  );
}
