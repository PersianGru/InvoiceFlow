"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { cx } from "@/components/ui";
import { useApp } from "@/lib/app-context";

export default function SettingsLayout({ children }: { children: ReactNode }) {
  const { t, user } = useApp();
  const pathname = usePathname();
  if (user?.role !== "firm_owner") return <p className="text-sm text-neutral-600">{t("common.error")}</p>;
  const items: Array<{ href: string; key: "nav.settings.license" | "nav.settings.pipeline" | "nav.settings.users" | "nav.settings.export_templates" | "nav.settings.api_keys" | "nav.settings.webhooks" | "nav.settings.channels" | "nav.settings.sso" | "nav.settings.connectors" }> = [
    { href: "/settings/license", key: "nav.settings.license" },
    { href: "/settings/pipeline", key: "nav.settings.pipeline" },
    { href: "/settings/users", key: "nav.settings.users" },
    { href: "/settings/export-templates", key: "nav.settings.export_templates" },
    { href: "/settings/api-keys", key: "nav.settings.api_keys" },
    { href: "/settings/webhooks", key: "nav.settings.webhooks" },
    { href: "/settings/channels", key: "nav.settings.channels" },
    { href: "/settings/sso", key: "nav.settings.sso" },
    { href: "/settings/connectors", key: "nav.settings.connectors" },
  ];
  return (
    <div className="grid gap-4 md:grid-cols-[12rem_1fr]">
      <nav aria-label={t("nav.settings")} className="flex gap-1 md:flex-col">
        {items.map((i) => (
          <Link key={i.href} href={i.href} aria-current={pathname === i.href ? "page" : undefined} className={cx("rounded-md px-3 py-1.5 text-sm", pathname === i.href ? "bg-neutral-200 font-medium dark:bg-neutral-800" : "hover:bg-neutral-100 dark:hover:bg-neutral-800")}>
            {t(i.key)}
          </Link>
        ))}
      </nav>
      <div>{children}</div>
    </div>
  );
}
