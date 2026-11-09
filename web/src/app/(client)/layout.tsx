"use client";
// The client portal shell: tiny header, no admin navigation, mobile-first.
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Button, Select, Spinner, cx } from "@/components/ui";
import { LOCALES, LOCALE_NAMES, type Locale } from "@/i18n";
import { post } from "@/lib/api";
import { useApp } from "@/lib/app-context";

export default function ClientLayout({ children }: { children: ReactNode }) {
  const { ready, user, firm, intakeOn, locale, setLocale, theme, toggleTheme, t, refreshSession } = useApp();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (ready && !user) router.replace("/login");
    else if (ready && user?.mfa_setup_required) router.replace("/setup-2fa");
  }, [ready, user, router]);

  if (!ready || !user) {
    return (
      <main className="flex flex-1 items-center justify-center p-8">
        <Spinner label={t("common.loading")} />
      </main>
    );
  }
  const paused = intakeOn === false;
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
        {t("nav.skip")}
      </a>
      <header className="sticky top-0 z-10 border-b border-neutral-200 bg-white/95 backdrop-blur dark:border-neutral-800 dark:bg-neutral-900/95">
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-2">
          <Link href="/my" className="text-lg font-semibold">
            InvoiceFlow
          </Link>
          <span className="truncate text-xs text-neutral-500">{firm?.name}</span>
          <div className="ml-auto flex items-center gap-1">
            <Select aria-label={t("locale.label")} className="w-auto py-1 text-xs" value={locale} onChange={(e) => setLocale(e.target.value as Locale)}>
              {LOCALES.map((l) => (
                <option key={l} value={l}>
                  {LOCALE_NAMES[l]}
                </option>
              ))}
            </Select>
            <Button size="sm" variant="secondary" aria-label={t("theme.toggle")} onClick={toggleTheme}>
              {theme === "dark" ? "☾" : "☀"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
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
      {paused ? (
        <div role="alert" className="border-b border-amber-300 bg-amber-50 px-4 py-2 text-center text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
          {t("intake.paused")}
        </div>
      ) : null}
      <main id="main" className={cx("mx-auto w-full max-w-3xl flex-1 px-4 py-4", pathname.startsWith("/my/capture") && "max-w-none p-0")}>
        {children}
      </main>
    </div>
  );
}
