"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Select, Spinner } from "@/components/ui";
import { formatDate, formatMoney } from "@/i18n";
import { ApiError, get, post, put, upload, type BankAccount, type BankFeed, type BankSummary, type BankTransaction, type ClientCompany, type DocumentPage, type PaymentMatch, type StatementImport } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { bestSuggestion, matchStatusTone, scoreTone, signedAmount } from "@/lib/bank";
import { useFetch } from "@/lib/use-fetch";

const REQUISITION_KEY = "invoiceflow.bank.requisition";

export default function BankPage() {
  const { t, locale, license, refreshLicense, user } = useApp();
  const summary = useFetch<BankSummary>(() => get<BankSummary>("/api/v1/bank/summary"), []);
  const accounts = useFetch<BankAccount[]>(() => get<BankAccount[]>("/api/v1/bank/accounts"), []);
  const suggestions = useFetch<PaymentMatch[]>(() => get<PaymentMatch[]>("/api/v1/bank/matches", { status: "suggested" }), []);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const transactions = useFetch<BankTransaction[]>(() => get<BankTransaction[]>("/api/v1/bank/transactions", { status: statusFilter || undefined, limit: 200 }), [statusFilter]);
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const openDocs = useFetch<DocumentPage>(() => get<DocumentPage>("/api/v1/documents", { limit: 100 }), []);
  const feeds = useFetch<BankFeed[]>(() => get<BankFeed[]>("/api/v1/bank/feeds"), []);
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [importClient, setImportClient] = useState("");
  const licensed = license?.core_on ?? true;
  const isOwner = user?.role === "firm_owner";

  async function reloadAll() {
    await Promise.all([summary.reload(), accounts.reload(), suggestions.reload(), transactions.reload()]);
  }

  async function run(fn: () => Promise<string | void>) {
    setBusy(true);
    setFlash(null);
    try {
      const text = await fn();
      if (text) setFlash({ tone: "success", text });
      await reloadAll();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
    }
  }

  async function importStatement(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    if (importClient) form.append("client_company_id", importClient);
    await run(async () => {
      const r = await upload<StatementImport>("/api/v1/bank/statements", form);
      setFile(null);
      return t("bank.import.done", { n: r.new_transactions, d: r.duplicates, m: r.matched, s: r.suggested });
    });
  }

  const docLabel = (m: PaymentMatch) => `${m.invoice_number ?? m.document_id.slice(0, 8)} · ${m.supplier_name ?? ""} · ${m.gross_total ?? ""}`;

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">{t("bank.title")}</h1>
      <p className="mb-3 text-sm text-neutral-600">{t("bank.help")}</p>
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(["accounts", "unmatched", "suggested", "matched"] as const).map((k) => (
          <Card key={k}>
            <p className="text-xs uppercase text-neutral-600">{t(`bank.summary.${k}` as "bank.summary.accounts")}</p>
            <p className="text-2xl font-semibold tabular-nums">{summary.data ? summary.data[k] : "…"}</p>
          </Card>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title={t("bank.suggestions")}>
            {suggestions.loading && !suggestions.data ? <Spinner label={t("common.loading")} /> : null}
            {suggestions.data && suggestions.data.length === 0 ? <p className="text-sm text-neutral-600">{t("bank.suggestions.empty")}</p> : null}
            {suggestions.data && suggestions.data.length > 0 ? (
              <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {suggestions.data.map((m) => (
                  <li key={m.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                    <Badge tone={scoreTone(m.score)}>{Math.round(m.score * 100)}%</Badge>
                    <a className="font-medium underline" href={`/documents/${m.document_id}`}>
                      {docLabel(m)}
                    </a>
                    <span className="text-neutral-600">{formatMoney(locale, m.amount, null)}</span>
                    <span className="text-xs text-neutral-500">{m.reasons.map((r) => t(`bank.reason.${r}` as "bank.reason.amount")).join(", ")}</span>
                    <span className="ml-auto flex gap-1">
                      <Button size="sm" disabled={busy || !licensed} onClick={() => run(async () => void (await post(`/api/v1/bank/matches/${m.id}/confirm`)))}>
                        {t("bank.confirm")}
                      </Button>
                      <Button size="sm" variant="secondary" disabled={busy || !licensed} onClick={() => run(async () => void (await post(`/api/v1/bank/matches/${m.id}/reject`)))}>
                        {t("bank.reject")}
                      </Button>
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </Card>
          <Card
            title={t("bank.transactions")}
            actions={
              <Select aria-label={t("bank.col.status")} className="w-auto py-1 text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="">{t("common.all")}</option>
                {(["unmatched", "suggested", "matched", "ignored"] as const).map((s) => (
                  <option key={s} value={s}>
                    {t(`bank.status.${s}` as "bank.status.unmatched")}
                  </option>
                ))}
              </Select>
            }
          >
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-neutral-600">
                  <tr>
                    <th className="p-2">{t("bank.col.date")}</th>
                    <th className="p-2">{t("bank.col.counterparty")}</th>
                    <th className="p-2">{t("bank.col.remittance")}</th>
                    <th className="p-2 text-right">{t("bank.col.amount")}</th>
                    <th className="p-2">{t("bank.col.status")}</th>
                    <th className="p-2" />
                  </tr>
                </thead>
                <tbody>
                  {(transactions.data ?? []).map((tx) => {
                    const best = bestSuggestion(tx);
                    return (
                      <tr key={tx.id} className="border-t border-neutral-100 align-top dark:border-neutral-800">
                        <td className="whitespace-nowrap p-2">{formatDate(locale, tx.booking_date)}</td>
                        <td className="p-2">
                          {tx.counterparty_name ?? "—"}
                          {tx.counterparty_iban ? <span className="block font-mono text-xs text-neutral-500">{tx.counterparty_iban}</span> : null}
                        </td>
                        <td className="max-w-xs p-2 text-xs text-neutral-700 dark:text-neutral-300">{tx.remittance_info ?? "—"}</td>
                        <td className="whitespace-nowrap p-2 text-right tabular-nums">{signedAmount(tx.amount, (v) => formatMoney(locale, v, tx.currency))}</td>
                        <td className="p-2">
                          <Badge tone={matchStatusTone(tx.match_status)}>{t(`bank.status.${tx.match_status}` as "bank.status.unmatched")}</Badge>
                          {best ? <span className="block text-xs text-neutral-500">{docLabel(best)}</span> : null}
                          {tx.match_status === "matched" && tx.matches[0] ? (
                            <a className="block text-xs underline" href={`/documents/${tx.matches[0].document_id}`}>
                              {docLabel(tx.matches[0])}
                            </a>
                          ) : null}
                        </td>
                        <td className="whitespace-nowrap p-2">
                          {tx.match_status !== "matched" ? (
                            <span className="flex flex-col gap-1">
                              <Select
                                aria-label={t("bank.match.manual")}
                                className="w-44 py-0.5 text-xs"
                                value=""
                                disabled={busy || !licensed}
                                onChange={(e) => {
                                  const documentId = e.target.value;
                                  if (documentId) void run(async () => void (await post(`/api/v1/bank/transactions/${tx.id}/match`, { document_id: documentId })));
                                }}
                              >
                                <option value="">{t("bank.match.pick")}</option>
                                {(openDocs.data?.items ?? [])
                                  .filter((d) => d.payment_status !== "paid" && d.invoice_number)
                                  .map((d) => (
                                    <option key={d.id} value={d.id}>
                                      {d.invoice_number} · {d.supplier_name ?? ""} · {d.gross_total ?? ""}
                                    </option>
                                  ))}
                              </Select>
                              <span className="flex gap-1">
                                <Button size="sm" variant="secondary" disabled={busy || !licensed} onClick={() => run(async () => void (await post(`/api/v1/bank/transactions/${tx.id}/rematch`)))}>
                                  {t("bank.rematch")}
                                </Button>
                                <Button size="sm" variant="ghost" disabled={busy || !licensed} onClick={() => run(async () => void (await post(`/api/v1/bank/transactions/${tx.id}/ignore`)))}>
                                  {tx.match_status === "ignored" ? t("bank.unignore") : t("bank.ignore")}
                                </Button>
                              </span>
                            </span>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                  {transactions.data && transactions.data.length === 0 ? (
                    <tr>
                      <td className="p-6 text-center text-neutral-600" colSpan={6}>
                        {t("bank.transactions.empty")}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
        <div className="space-y-4">
          <Card title={t("bank.import")}>
            <form onSubmit={importStatement}>
              <Field id="stmt-file" label={t("bank.import.file")}>
                <input id="stmt-file" type="file" accept=".xml,.sta,.txt,.mt940,.940,application/xml,text/plain" className="block w-full text-sm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </Field>
              <Field id="stmt-client" label={t("bank.import.client")}>
                <Select id="stmt-client" value={importClient} onChange={(e) => setImportClient(e.target.value)}>
                  <option value="">{t("common.none")}</option>
                  {(clients.data ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Button type="submit" disabled={busy || !file || !licensed}>
                {t("bank.import.button")}
              </Button>
            </form>
          </Card>
          <Card title={t("bank.accounts")}>
            {(accounts.data ?? []).length === 0 ? <p className="text-sm text-neutral-600">{t("common.none")}</p> : null}
            <ul className="space-y-2 text-sm">
              {(accounts.data ?? []).map((a) => (
                <li key={a.id}>
                  <span className="font-mono text-xs">{a.iban}</span>
                  <span className="block text-xs text-neutral-600">
                    {a.name || a.source} · {a.currency} · {a.transactions} / {a.unmatched} {t("bank.status.unmatched")}
                    {a.last_sync_at ? ` · ${formatDate(locale, a.last_sync_at)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
          {isOwner ? <FeedCard key={JSON.stringify(feeds.data ?? [])} feeds={feeds.data ?? []} clients={clients.data ?? []} licensed={licensed} onChange={async () => Promise.all([feeds.reload(), reloadAll()]).then(() => undefined)} /> : null}
        </div>
      </div>
    </div>
  );
}

function FeedCard({ feeds, clients, licensed, onChange }: { feeds: BankFeed[]; clients: ClientCompany[]; licensed: boolean; onChange: () => Promise<void> }) {
  const { t, refreshLicense } = useApp();
  const feed = feeds.find((f) => f.provider === "gocardless");
  const [form, setForm] = useState({ enabled: feed?.enabled ?? false, secret_id: feed?.config.secret_id ?? "", secret_key: "" });
  const [country, setCountry] = useState("DE");
  const [institutions, setInstitutions] = useState<Array<{ id: string; name: string }>>([]);
  const [institution, setInstitution] = useState("");
  const [linkClient, setLinkClient] = useState("");
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingRequisition, setPendingRequisition] = useState<string | null>(() => {
    try {
      return typeof window === "undefined" ? null : window.localStorage.getItem(REQUISITION_KEY);
    } catch {
      return null; // storage unavailable
    }
  });

  async function run(fn: () => Promise<string | void>) {
    setBusy(true);
    setFlash(null);
    try {
      const text = await fn();
      if (text) setFlash({ tone: "success", text });
      await onChange();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t("bank.feed")}>
      <p className="mb-2 text-sm text-neutral-600">{t("bank.feed.help")}</p>
      {flash ? (
        <Notice tone={flash.tone} className="mb-3">
          {flash.text}
        </Notice>
      ) : null}
      {feed?.last_error ? (
        <Notice tone="warning" className="mb-3">
          {feed.last_error}
        </Notice>
      ) : null}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await put("/api/v1/bank/feeds/gocardless", { enabled: form.enabled, config: { secret_id: form.secret_id, secret_key: form.secret_key } });
            setForm((f) => ({ ...f, secret_key: "" }));
            return t("bank.feed.saved");
          });
        }}
      >
        <Field id="gc-id" label={t("bank.feed.secret_id")}>
          <Input id="gc-id" autoComplete="off" value={form.secret_id} onChange={(e) => setForm({ ...form, secret_id: e.target.value })} />
        </Field>
        <Field id="gc-key" label={t("bank.feed.secret_key")} hint={feed?.config.secret_key ? t("connectors.secret_stored") : undefined}>
          <Input id="gc-key" type="password" autoComplete="off" value={form.secret_key} onChange={(e) => setForm({ ...form, secret_key: e.target.value })} />
        </Field>
        <label className="mb-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
          {t("bank.feed.enable")}
        </label>
        <Button type="submit" disabled={busy || !licensed}>
          {t("common.save")}
        </Button>
      </form>
      {feed?.enabled ? (
        <div className="mt-4 space-y-2 border-t border-neutral-100 pt-3 dark:border-neutral-800">
          <Field id="gc-country" label={t("bank.feed.country")}>
            <Input
              id="gc-country"
              maxLength={2}
              value={country}
              onChange={(e) => setCountry(e.target.value.toUpperCase())}
              onBlur={() => void run(async () => setInstitutions(await get<Array<{ id: string; name: string }>>("/api/v1/bank/feeds/gocardless/institutions", { country })))}
            />
          </Field>
          <Field id="gc-inst" label={t("bank.feed.institution")}>
            <Select id="gc-inst" value={institution} onChange={(e) => setInstitution(e.target.value)}>
              <option value="">—</option>
              {institutions.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="gc-client" label={t("bank.import.client")}>
            <Select id="gc-client" value={linkClient} onChange={(e) => setLinkClient(e.target.value)}>
              <option value="">{t("common.none")}</option>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              disabled={busy || !institution}
              onClick={() =>
                run(async () => {
                  const r = await post<{ id: string; link: string }>("/api/v1/bank/feeds/gocardless/requisitions", { institution_id: institution, client_company_id: linkClient || null });
                  try {
                    window.localStorage.setItem(REQUISITION_KEY, r.id);
                  } catch {
                    /* storage unavailable */
                  }
                  setPendingRequisition(r.id);
                  window.open(r.link, "_blank", "noopener");
                })
              }
            >
              {t("bank.feed.link")}
            </Button>
            {pendingRequisition ? (
              <Button
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await post(`/api/v1/bank/feeds/gocardless/requisitions/${pendingRequisition}/complete`);
                    try {
                      window.localStorage.removeItem(REQUISITION_KEY);
                    } catch {
                      /* storage unavailable */
                    }
                    setPendingRequisition(null);
                  })
                }
              >
                {t("bank.feed.complete")}
              </Button>
            ) : null}
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const r = await post<{ transactions: number; matched: number; suggested: number }>("/api/v1/bank/feeds/sync");
                  return t("bank.feed.synced", { t: r.transactions, m: r.matched, s: r.suggested });
                })
              }
            >
              {t("bank.feed.sync")}
            </Button>
          </div>
        </div>
      ) : null}
    </Card>
  );
}
