"use client";
// One inbound domain: status, the TXT record to create (copyable), the MX hosts found, a check button.
import { Badge, Button } from "@/components/ui";
import { formatDateTime } from "@/i18n";
import { useApp } from "@/lib/app-context";

export interface DomainView {
  id: string;
  kind: "root" | "platform_sub" | "custom";
  domain: string;
  label: string | null;
  status: "pending" | "verified" | "warning";
  is_default: boolean;
  txt_name: string;
  txt_value: string | null;
  mx_hosts: string | null;
  verified_at: string | null;
  last_check_at: string | null;
  last_check_error: string | null;
  retired_at: string | null;
  grace_until: string | null;
}

const TONE = { pending: "warning", verified: "success", warning: "warning" } as const;

export function DomainCard({ d, onCheck, busy }: { d: DomainView; onCheck?: () => void; busy?: boolean }) {
  const { t, locale } = useApp();
  return (
    <div className="rounded-md border border-neutral-200 p-3 text-sm dark:border-neutral-800">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-mono font-medium">{d.domain}</span>
        <Badge tone={d.retired_at ? "neutral" : TONE[d.status]}>{d.retired_at ? t("domain.retired") : t(`domain.status.${d.status}`)}</Badge>
        {d.is_default ? <Badge tone="info">{t("domain.default")}</Badge> : null}
        {d.grace_until ? <span className="text-xs">{t("domain.grace_until", { date: formatDateTime(locale, d.grace_until) })}</span> : null}
      </div>
      {d.txt_value && !d.retired_at ? (
        <div className="mb-2">
          <p className="mb-1 text-xs text-neutral-600 dark:text-neutral-400">{t("domain.txt_intro")}</p>
          <dl className="grid grid-cols-[6rem_1fr_auto] items-center gap-1 text-xs">
            <dt>{t("domain.txt_name")}</dt>
            <dd className="break-all font-mono">{d.txt_name}</dd>
            <dd>
              <Button size="sm" variant="ghost" onClick={() => void navigator.clipboard?.writeText(d.txt_name)}>
                {t("common.copy")}
              </Button>
            </dd>
            <dt>{t("domain.txt_value")}</dt>
            <dd className="break-all font-mono">{d.txt_value}</dd>
            <dd>
              <Button size="sm" variant="ghost" onClick={() => void navigator.clipboard?.writeText(d.txt_value ?? "")}>
                {t("common.copy")}
              </Button>
            </dd>
          </dl>
        </div>
      ) : null}
      <p className="text-xs text-neutral-600 dark:text-neutral-400">
        {t("domain.mx")}: {d.mx_hosts ?? t("domain.mx_none")}
        {d.last_check_at ? ` · ${t("domain.checked", { date: formatDateTime(locale, d.last_check_at) })}` : ""}
      </p>
      {d.status === "warning" ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">{t("domain.warning")}</p> : null}
      {onCheck && !d.retired_at ? (
        <Button className="mt-2" size="sm" variant="secondary" disabled={busy} onClick={onCheck}>
          {t("domain.check")}
        </Button>
      ) : null}
    </div>
  );
}
