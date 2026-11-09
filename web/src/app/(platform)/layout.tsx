"use client";
// The platform admin's shell: no firm data here; firms are opened one at a time.
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Button, Select, Spinner, cx } from "@/components/ui";
import { LOCALES, LOCALE_NAMES, type Locale } from "@/i18n";
import { post } from "@/lib/api";
import { useApp } from "@/lib/app-context";

const NAV = [
  { href: "/platform", key: "platform.nav.overview" },
  { href: "/platform/settings", key: "platform.nav.settings" },
  { href: "/platform/backups", key: "platform.nav.backups" },
  { href: "/platform/license", key: "platform.nav.license" },
] as const;

export default function PlatformLayout({ children }: { children: ReactNode }) {
  const { ready, user, locale, setLocale, theme, toggleTheme, t, refreshSession } = useApp();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (ready && !user) router.replace("/login");
    else if (ready && user?.mfa_setup_required) router.replace("/setup-2fa");
    else if (ready && user && user.role !== "super_admin") router.replace("/");
  }, [ready, user, router]);

  if (!ready || !user || user.role !== "super_admin") {
    return (
      <main className="flex flex-1 items-center justify-center p-8">
        <Spinner label={t("common.loading")} />
      </main>
    );
  }
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-2">
          <Link href="/platform" className="mr-4 text-lg font-semibold">
            {t("app.name")} · {t("platform.title")}
          </Link>
          <nav aria-label={t("platform.title")} className="flex flex-wrap gap-1">
            {NAV.map((n) => {
              const active = n.href === "/platform" ? pathname === "/platform" : pathname.startsWith(n.href);
              return (
                <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined} className={cx("rounded-md px-3 py-1.5 text-sm", active ? "bg-neutral-200 font-medium dark:bg-neutral-800" : "hover:bg-neutral-100 dark:hover:bg-neutral-800")}>
                  {t(n.key)}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2 text-sm">
            <span className="hidden text-neutral-600 sm:inline dark:text-neutral-400">{user.email}</span>
            <Select aria-label={t("locale.label")} value={locale} onChange={(e) => setLocale(e.target.value as Locale)} className="w-auto py-1">
              {LOCALES.map((l) => (
                <option key={l} value={l}>
                  {LOCALE_NAMES[l]}
                </option>
              ))}
            </Select>
            <Button variant="ghost" size="sm" aria-label={t("theme.toggle")} aria-pressed={theme === "dark"} onClick={toggleTheme}>
              {theme === "dark" ? "☾" : "☀"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={async () => {
                await post("/api/v1/auth/logout");
                await refreshSession();
                router.replace("/login");
              }}
            >
              {t("nav.signout")}
            </Button>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-4">
        {children}
      </main>
    </div>
  );
}
