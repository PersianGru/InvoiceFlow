"use client";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, Input, Notice, Select } from "@/components/ui";
import { ApiError, get, post, type ClientCompany, type User } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

const ROLES = ["accountant", "junior_accountant", "client_admin", "client_user"] as const;

export default function UsersPage() {
  const { t, refreshLicense } = useApp();
  const users = useFetch<User[]>(() => get<User[]>("/api/v1/users"), []);
  const clients = useFetch<ClientCompany[]>(() => get<ClientCompany[]>("/api/v1/client-companies"), []);
  const [form, setForm] = useState({ email: "", display_name: "", role: "accountant" as (typeof ROLES)[number], password: "", client_company_id: "" });
  const [flash, setFlash] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const isClientRole = form.role === "client_admin" || form.role === "client_user";

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await post("/api/v1/users/invite", { ...form, client_company_id: isClientRole ? form.client_company_id || null : null });
      setFlash({ tone: "success", text: t("users.invited") });
      setForm({ ...form, email: "", display_name: "", password: "" });
      await users.reload();
    } catch (err) {
      setFlash({ tone: "danger", text: err instanceof ApiError ? err.message : t("common.error") });
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <h1 className="mb-3 text-xl font-semibold">{t("users.title")}</h1>
        <div className="overflow-x-auto rounded-md border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              <tr>
                <th className="p-2">{t("users.email")}</th>
                <th className="p-2">{t("users.name")}</th>
                <th className="p-2">{t("users.role")}</th>
                <th className="p-2">{t("users.totp")}</th>
              </tr>
            </thead>
            <tbody>
              {(users.data ?? []).map((u) => (
                <tr key={u.id} className="border-t border-neutral-100 dark:border-neutral-800">
                  <td className="p-2">{u.email}</td>
                  <td className="p-2">{u.display_name}</td>
                  <td className="p-2">
                    <Badge>{t(`role.${u.role}` as "role.accountant")}</Badge>
                  </td>
                  <td className="p-2">{u.totp_enabled ? t("common.yes") : t("common.no")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Card title={t("users.invite")}>
        {flash ? (
          <Notice tone={flash.tone} className="mb-3">
            {flash.text}
          </Notice>
        ) : null}
        <form onSubmit={submit}>
          <Field id="u-email" label={t("users.email")}>
            <Input id="u-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Field>
          <Field id="u-name" label={t("users.name")}>
            <Input id="u-name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </Field>
          <Field id="u-role" label={t("users.role")}>
            <Select id="u-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as (typeof ROLES)[number] })}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {t(`role.${r}`)}
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
          <Field id="u-password" label={t("users.password")}>
            <Input id="u-password" type="password" required minLength={12} autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </Field>
          <Button type="submit">{t("users.invite")}</Button>
        </form>
      </Card>
    </div>
  );
}
