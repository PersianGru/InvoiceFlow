// License presentation helpers (pure, tested). Entitlement decisions are never made here: the
// server enforces them; the UI only explains the state the API reports.
import type { LicenseView, RefreshOutcome } from "./api";
import type { Locale, MessageKey } from "@/i18n";
import { t } from "@/i18n";

export type BannerLevel = "info" | "warning" | "error";

export interface BannerModel {
  level: BannerLevel;
  text: string;
  cta: { labelKey: MessageKey; href: string } | null;
}

/** A valid token that carries no `core` tool (a license issued from a plan without it) is reported
 * as `expired` with reason `core_not_included`; it reads differently from a license that ended. */
export function coreNotIncluded(view: Pick<LicenseView, "state" | "reason">): boolean {
  return view.state === "expired" && view.reason === "core_not_included";
}

export function stateKey(view: Pick<LicenseView, "state" | "reason">): MessageKey {
  if (coreNotIncluded(view)) return "license.state.core_not_included";
  return `license.state.${view.state}` as MessageKey;
}

/** The message shown after "Check now" (spec §12: the hub is asked at most every 15 minutes). */
export function refreshFlash(outcome: RefreshOutcome, locale: Locale): { tone: "success" | "warning"; text: string } {
  if (outcome.ok) return { tone: "success", text: t(locale, "license.refresh.ok") };
  const time = outcome.retry_at ? new Intl.DateTimeFormat(locale, { timeStyle: "short" }).format(new Date(outcome.retry_at * 1000)) : null;
  return { tone: "warning", text: time ? `${outcome.message} ${t(locale, "license.refresh.next", { time })}` : outcome.message };
}

/** The banner shown on every admin page. Null when the license is valid and no trial runs. */
export function bannerFor(view: LicenseView | null, locale: Locale): BannerModel | null {
  if (!view) return null;
  if (view.state === "grace") {
    return { level: "warning", text: t(locale, "license.banner.grace"), cta: { labelKey: "license.open_page", href: "/settings/license" } };
  }
  if (coreNotIncluded(view)) {
    return { level: "error", text: t(locale, "license.banner.core_not_included"), cta: { labelKey: "license.cta.buy", href: view.store_links.buy } };
  }
  if (view.state === "expired" || view.state === "unlicensed" || view.state === "invalid") {
    const base = t(locale, `license.banner.${view.state}` as MessageKey);
    const support = view.hub_error && ["license_suspended", "instance_blocked", "license_revoked"].includes(view.hub_error) ? ` ${t(locale, "license.banner.contact_support")}` : "";
    const href = view.state === "unlicensed" && !view.has_key ? view.store_links.trial : "/settings/license";
    return { level: "error", text: base + support, cta: { labelKey: view.state === "unlicensed" && !view.has_key ? "license.cta.start_trial" : "license.open_page", href } };
  }
  if (view.trial && view.trial_days_left !== null) {
    return { level: "info", text: t(locale, "license.trial_days_left", { days: view.trial_days_left }), cta: { labelKey: "license.cta.buy", href: view.store_links.buy } };
  }
  return null;
}

/** Support messages (spec §8.5): plain text only, unseen ones first. */
export function unseenMessages(view: LicenseView | null): LicenseView["messages"] {
  if (!view) return [];
  return [...view.messages].sort((a, b) => Number(a.seen) - Number(b.seen));
}

export function expiresLabel(view: LicenseView, locale: Locale): string {
  if (view.expires_at === null) return t(locale, "license.never_expires");
  return new Intl.DateTimeFormat(locale, { dateStyle: "long" }).format(new Date(view.expires_at * 1000));
}

export function entitlementRows(view: LicenseView): Array<{ key: string; value: string }> {
  const f = view.features;
  const limit = (k: string) => {
    const v = f[k];
    return typeof v === "number" && v > 0 ? String(v) : "∞";
  };
  const bool = (k: string) => (f[k] === true ? "✓" : "–");
  return [
    { key: "max_client_companies", value: limit("max_client_companies") },
    { key: "max_documents_per_month", value: limit("max_documents_per_month") },
    { key: "max_staff_users", value: limit("max_staff_users") },
    { key: "channels", value: bool("channels") },
    { key: "connectors", value: bool("connectors") },
    { key: "cloud_ai", value: bool("cloud_ai") },
    { key: "api", value: bool("api") },
    { key: "sso", value: bool("sso") },
  ];
}
