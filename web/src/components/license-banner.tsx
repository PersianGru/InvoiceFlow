"use client";
// The license banner on every admin page: state, trial days, grace warning, support
// messages (plain text, never HTML). Hidden when the license is valid and nothing needs attention.
import Link from "next/link";

import { bannerFor, unseenMessages } from "@/lib/license";
import { useApp } from "@/lib/app-context";
import { post } from "@/lib/api";
import { Button, Notice } from "./ui";

export function LicenseBanner() {
  const { license, locale, t, user, refreshLicense } = useApp();
  if (!user || user.role === "super_admin" || user.role === "client_admin" || user.role === "client_user") return null;
  const banner = bannerFor(license, locale);
  const messages = unseenMessages(license).filter((m) => !m.seen);
  if (!banner && messages.length === 0) return null;
  const canManage = user.role === "firm_owner";
  return (
    <div className="space-y-2 px-4 pt-3" data-testid="license-banner">
      {banner ? (
        <Notice tone={banner.level === "error" ? "danger" : banner.level === "warning" ? "warning" : "info"} role={banner.level === "error" ? "alert" : "status"}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>{banner.text}</span>
            {banner.cta && canManage ? (
              banner.cta.href.startsWith("http") ? (
                <a className="font-medium underline" href={banner.cta.href} target="_blank" rel="noreferrer">
                  {t(banner.cta.labelKey)}
                </a>
              ) : (
                <Link className="font-medium underline" href={banner.cta.href}>
                  {t(banner.cta.labelKey)}
                </Link>
              )
            ) : null}
          </div>
        </Notice>
      ) : null}
      {messages.map((m) => (
        <Notice key={m.id} tone={m.level === "warning" ? "warning" : "info"}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            {/* plain text: React escapes it; never dangerouslySetInnerHTML */}
            <span>{m.text}</span>
            {user.firm_id ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  await post(`/api/v1/firms/${user.firm_id}/license/messages/${encodeURIComponent(m.id)}/seen`);
                  await refreshLicense();
                }}
              >
                {t("license.messages.dismiss")}
              </Button>
            ) : null}
          </div>
        </Notice>
      ))}
    </div>
  );
}
