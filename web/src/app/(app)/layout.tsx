"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { LicenseBanner } from "@/components/license-banner";
import { Button, Select, Spinner, cx } from "@/components/ui";
import { LOCALES, LOCALE_NAMES, type Locale } from "@/i18n";
import { post } from "@/lib/api";
import { useApp } from "@/lib/app-context";

const NAV: Array<{ href: string; key: "nav.inbox" | "nav.exports" | "nav.bank" | "nav.reporting" | "nav.clients" | "nav.suppliers" | "nav.stats" | "nav.audit" | "nav.trash"; roles: string[] }> = [
  { href: "/inbox", key: "nav.inbox", roles: ["firm_owner", "accountant", "junior_accountant", "client_admin", "client_user"] },
  { href: "/exports", key: "nav.exports", roles: ["firm_owner", "accountant", "junior_accountant"] },
  { href: "/bank", key: "nav.bank", roles: ["firm_owner", "accountant", "junior_accountant"] },
  { href: "/reporting", key: "nav.reporting", roles: ["firm_owner", "accountant"] },
  { href: "/clients", key: "nav.clients", roles: ["firm_owner", "accountant", "junior_accountant"] },
  { href: "/suppliers", key: "nav.suppliers", roles: ["firm_owner", "accountant", "junior_accountant"] },
  { href: "/stats", key: "nav.stats", roles: ["firm_owner", "accountant"] },
  { href: "/audit", key: "nav.audit", roles: ["firm_owner", "accountant"] },
  { href: "/trash", key: "nav.trash", roles: ["firm_owner", "accountant"] },
];

export default function AppLayout({ children }: { children: ReactNode }) {
  const { ready, user, firm, locale, setLocale, theme, toggleTheme, t, refreshSession } = useApp();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (ready && !user) router.replace("/login");
    else if (ready && user?.mfa_setup_required) router.replace("/setup-2fa");
    else if (ready && user && (user.role === "client_admin" || user.role === "client_user")) router.replace("/my");
    else if (ready && user?.role === "super_admin" && !user.acting_firm_id) router.replace("/platform");
  }, [ready, user, router]);

  if (!ready || !user) {
    return (
      <main className="flex flex-1 items-center justify-center p-8">
        <Spinner label={t("common.loading")} />
      </main>
    );
  }
  const isOwner = user.role === "firm_owner" || user.role === "super_admin";
  const settingsHref = isOwner ? "/settings/license" : user.role === "accountant" ? "/settings/users" : null;
  const items = NAV.filter((n) => n.roles.includes(user.role) || user.role === "super_admin");

  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
        {t("nav.skip")}
      </a>
      <header className="border-b border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-2">
          <Link href="/inbox" className="mr-4 text-lg font-semibold">
            {t("app.name")}
          </Link>
          <nav aria-label="Main" className="flex flex-wrap gap-1">
            {items.map((n) => (
              <Link key={n.href} href={n.href} aria-current={pathname.startsWith(n.href) ? "page" : undefined} className={cx("rounded-md px-3 py-1.5 text-sm", pathname.startsWith(n.href) ? "bg-neutral-200 font-medium dark:bg-neutral-800" : "hover:bg-neutral-100 dark:hover:bg-neutral-800")}>
                {t(n.key)}
              </Link>
            ))}
            {settingsHref ? (
              <Link href={settingsHref} aria-current={pathname.startsWith("/settings") ? "page" : undefined} className={cx("rounded-md px-3 py-1.5 text-sm", pathname.startsWith("/settings") ? "bg-neutral-200 font-medium dark:bg-neutral-800" : "hover:bg-neutral-100 dark:hover:bg-neutral-800")}>
                {t("nav.settings")}
              </Link>
            ) : null}
          </nav>
          <div className="ml-auto flex items-center gap-2 text-sm">
            <span className="hidden text-neutral-600 sm:inline dark:text-neutral-400">{firm?.name ?? user.email}</span>
            <label className="sr-only" htmlFor="locale">
              {t("locale.label")}
            </label>
            <Select id="locale" value={locale} onChange={(e) => setLocale(e.target.value as Locale)} className="w-auto py-1">
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
      {user.role === "super_admin" && user.acting_firm_id ? (
        <div role="status" className="border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-2">
            <span>{t("platform.acting_banner", { firm: firm?.name ?? "" })}</span>
            <Button
              size="sm"
              variant="secondary"
              onClick={async () => {
                await post("/api/v1/platform/switch", { firm_id: null });
                await refreshSession();
                router.replace("/platform");
              }}
            >
              {t("platform.back")}
            </Button>
          </div>
        </div>
      ) : null}
      <LicenseBanner />
      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-4">
        {children}
      </main>
    </div>
  );
}
