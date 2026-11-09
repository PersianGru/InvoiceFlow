"use client";
// Choose client companies (empty = the whole firm). Used for API keys and webhook endpoints (F, G).
import type { ClientCompany } from "@/lib/api";
import { useApp } from "@/lib/app-context";

export function ClientPicker({ clients, value, onChange, id }: { clients: ClientCompany[]; value: string[]; onChange: (v: string[]) => void; id: string }) {
  const { t } = useApp();
  return (
    <fieldset className="mb-3" id={id}>
      <legend className="mb-1 text-sm font-medium">{t("scope.clients")}</legend>
      <p className="mb-1 text-xs text-neutral-600 dark:text-neutral-400">{value.length ? t("scope.some", { n: value.length }) : t("scope.all")}</p>
      <div className="max-h-40 overflow-y-auto">
        {clients.map((c) => (
          <label key={c.id} className="flex items-center gap-2 py-0.5 text-sm">
            <input type="checkbox" checked={value.includes(c.id)} onChange={() => onChange(value.includes(c.id) ? value.filter((x) => x !== c.id) : [...value, c.id])} />
            {c.name}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
