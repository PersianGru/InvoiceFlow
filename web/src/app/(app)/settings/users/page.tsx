"use client";
// User management: invite by link (email + copy link, no password field), resend, reset
// password, reset 2FA, change role, deactivate/reactivate, delete with justification; deleted users
// keep a sealed trace the firm owner can reveal.
import { useState, type FormEvent } from "react";

import { useReason } from "@/components/reason-dialog";
import { Badge, Button, Card, Field, Input, Notice, Select } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { ApiError, get, patch, post, type ClientCompany, type InviteOut, type Tombstone, type User } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

const STAFF_ROLES = ["firm_owner", "accountant", "junior_accountant"] as const;
const CLIENT_ROLES = ["client_admin", "client_user"] as const;
type Role = (typeof STAFF_ROLES)[number] | (typeof CLIENT_ROLES)[number];

const STATUS_TONE = { active: "success", invited: "info", deactivated: "warning", deleted: "neutral" } as const;

export default function UsersPage() {
  const { t, locale, user: me, refreshLicense } = useApp();
  const isOwner = me?.role === "firm_owner" || me?.role === "super_admin";
  const users = useFetch<User[]>(() => get<User[]>("/api/v1/users"), []);
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const deleted = useFetch<Tombstone[]>(() => (isOwner ? get<Tombstone[]>("/api/v1/users/deleted") : Promise.resolve([])), [isOwner]);
  const roles: Role[] = isOwner ? [...STAFF_ROLES, ...CLIENT_ROLES] : [...CLIENT_ROLES];
  const [form, setForm] = useState({ email: "", display_name: "", role: roles[1] ?? "client_user", client_company_id: "", send_email: true });
  const [flash, setFlash] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);
  const [link, setLink] = useState<InviteOut | null>(null);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [dialog, ask] = useReason();
  const isClientRole = form.role === "client_admin" || form.role === "client_user";

  async function act(fn: () => Promise<unknown>, ok: string) {
    setFlash(null);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "link" in (r as object)) setLink(r as InviteOut);
      setFlash({ tone: "success", text: ok });
      await users.reload();
      await deleted.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    }
  }

  async function invite(e: FormEvent) {
    e.preventDefault();
    await act(async () => {
      const r = await post<InviteOut>("/api/v1/users/invite", { ...form, client_company_id: isClientRole ? form.client_company_id || null : null });
      setForm({ ...form, email: "", display_name: "" });
      return r;
    }, t("users.invited"));
  }

  async function withReason(title: string, path: string, ok: string, danger = false) {
    const reason = await ask(title, { danger });
    if (reason) await act(() => post(path, { reason }), ok);
  }

  async function changeRole(u: User, role: string) {
    await act(() => patch(`/api/v1/users/${u.id}`, { role }), t("users.updated"));
  }

  const clientName = (id: string | null) => (clients.data ?? []).find((c) => c.id === id)?.name ?? "";

  return (
    <div className="space-y-4">
      {dialog}
      <h1 className="text-xl font-semibold">{t("users.title")}</h1>
      {flash ? (
        <Notice tone={flash.tone} role={flash.tone === "danger" ? "alert" : "status"}>
          {flash.text}
        </Notice>
      ) : null}
      {link ? (
        <Card title={link.email_sent ? t("users.link_sent") : t("users.link_not_sent")}>
          <p className="mb-2 text-sm">{t("users.link_intro", { email: link.user.email, date: formatDateTime(locale, link.expires_at) })}</p>
          <div className="flex gap-2">
            <Input readOnly value={link.link} aria-label={t("users.link")} onFocus={(e) => e.currentTarget.select()} />
            <Button variant="secondary" onClick={() => void navigator.clipboard?.writeText(link.link)}>
              {t("common.copy")}
            </Button>
            <Button variant="ghost" onClick={() => setLink(null)}>
              {t("common.cancel")}
            </Button>
          </div>
        </Card>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white lg:col-span-2 dark:border-neutral-800 dark:bg-neutral-900">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              <tr>
                <th className="p-2">{t("users.name")}</th>
                <th className="p-2">{t("users.role")}</th>
                <th className="p-2">{t("users.status")}</th>
                <th className="p-2">{t("users.totp")}</th>
                <th className="p-2">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {(users.data ?? []).map((u) => {
                const self = u.id === me?.id;
                const staff = (STAFF_ROLES as readonly string[]).includes(u.role);
                const manageable = !self && (isOwner || !staff);
                return (
                  <tr key={u.id} className="border-t border-neutral-100 align-top dark:border-neutral-800">
                    <td className="p-2">
                      <div className="font-medium">{u.display_name}</div>
                      <div className="text-xs text-neutral-600 dark:text-neutral-400">{u.email}</div>
                      {u.client_company_id ? <div className="text-xs">{clientName(u.client_company_id)}</div> : null}
                    </td>
                    <td className="p-2">
                      {manageable && isOwner ? (
                        <Select aria-label={t("users.role")} value={u.role} className="w-auto py-1" onChange={(e) => void changeRole(u, e.target.value)}>
                          {(staff ? STAFF_ROLES : CLIENT_ROLES).map((r) => (
                            <option key={r} value={r}>
                              {t(`role.${r}` as "role.accountant")}
                            </option>
                          ))}
                        </Select>
                      ) : (
                        <Badge>{t(`role.${u.role}` as "role.accountant")}</Badge>
                      )}
                    </td>
                    <td className="p-2">
                      <Badge tone={STATUS_TONE[u.status ?? "active"]}>{t(`users.status.${u.status ?? "active"}` as "users.status.active")}</Badge>
                    </td>
                    <td className="p-2">{u.totp_enabled ? t("common.yes") : t("common.no")}</td>
                    <td className="p-2">
                      {manageable ? (
                        <div className="flex flex-wrap gap-1">
                          {u.status === "invited" ? (
                            <Button size="sm" variant="secondary" onClick={() => void act(() => post(`/api/v1/users/${u.id}/resend-invite`), t("users.invited"))}>
                              {t("users.resend")}
                            </Button>
                          ) : null}
                          {u.status === "active" ? (
                            <Button size="sm" variant="secondary" onClick={() => void act(() => post(`/api/v1/users/${u.id}/reset-password`), t("users.reset_sent"))}>
                              {t("users.reset_password")}
                            </Button>
                          ) : null}
                          {isOwner && u.totp_enabled ? (
                            <Button size="sm" variant="secondary" onClick={() => void withReason(t("users.reset_2fa"), `/api/v1/users/${u.id}/reset-2fa`, t("users.updated"))}>
                              {t("users.reset_2fa")}
                            </Button>
                          ) : null}
                          {u.status === "deactivated" ? (
                            <Button size="sm" variant="secondary" onClick={() => void withReason(t("users.reactivate"), `/api/v1/users/${u.id}/reactivate`, t("users.updated"))}>
                              {t("users.reactivate")}
                            </Button>
                          ) : (
                            <Button size="sm" variant="secondary" onClick={() => void withReason(t("users.deactivate"), `/api/v1/users/${u.id}/deactivate`, t("users.updated"))}>
                              {t("users.deactivate")}
                            </Button>
                          )}
                          <Button size="sm" variant="danger" onClick={() => void withReason(t("users.delete"), `/api/v1/users/${u.id}/delete`, t("users.deleted"), true)}>
                            {t("users.delete")}
                          </Button>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <Card title={t("users.invite")}>
          <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">{t("users.invite_intro")}</p>
          <form onSubmit={invite}>
            <Field id="u-email" label={t("users.email")}>
              <Input id="u-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </Field>
            <Field id="u-name" label={t("users.name")}>
              <Input id="u-name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            </Field>
            <Field id="u-role" label={t("users.role")}>
              <Select id="u-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
                {roles.map((r) => (
                  <option key={r} value={r}>
                    {t(`role.${r}` as "role.accountant")}
                  </option>
                ))}
              </Select>
            </Field>
            {isClientRole ? (
              <Field id="u-client" label={t("users.client")}>
                <Select id="u-client" required value={form.client_company_id} onChange={(e) => setForm({ ...form, client_company_id: e.target.value })}>
                  <option value="">—</option>
                  {(clients.data ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : null}
            <label className="mb-3 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.send_email} onChange={(e) => setForm({ ...form, send_email: e.target.checked })} />
              {t("users.send_email")}
            </label>
            <Button type="submit">{t("users.invite")}</Button>
          </form>
        </Card>
      </div>
      {isOwner && (deleted.data ?? []).length > 0 ? (
        <Card title={t("users.deleted_title")}>
          <p className="mb-2 text-sm text-neutral-600 dark:text-neutral-400">{t("users.deleted_intro")}</p>
          <ul className="space-y-2 text-sm">
            {(deleted.data ?? []).map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{d.label}</span>
                <span className="text-neutral-600 dark:text-neutral-400">{formatDateTime(locale, d.deleted_at)}</span>
                <span>“{d.reason}”</span>
                {revealed[d.id] ? (
                  <Badge tone="warning">{revealed[d.id]}</Badge>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      void post<{ email: string; display_name: string }>(`/api/v1/users/deleted/${d.id}/reveal`).then((r) => setRevealed({ ...revealed, [d.id]: `${r.display_name} <${r.email}>` }))
                    }
                  >
                    {t("users.reveal")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
