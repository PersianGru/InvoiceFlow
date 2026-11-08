import { describe, expect, it } from "vitest";

import type { LicenseView } from "./api";
import { bannerFor, entitlementRows, expiresLabel, refreshFlash, stateKey, unseenMessages } from "./license";

function view(over: Partial<LicenseView>): LicenseView {
  return {
    state: "valid", reason: null, core_on: true, trial: false, trial_days_left: null, expires_at: 1800000000, grace_days: 14, kh: "ABCDE", plan: "standard", plan_name: "Standard",
    licensed_to: "Acme", items: {}, features: { max_client_companies: 0, max_documents_per_month: 500, channels: true, api: false }, use: "lease", next_refresh_at: null, verified_at: 0,
    hub_error: null, hub_message: null, installation_id: "1".repeat(38), installation_id_grouped: "11111 11111", has_key: true, messages: [],
    store_links: { buy: "https://store/?installation=1", trial: "https://store/?installation=1&trial=1", renew: "https://store/", offline: "https://store/offline" }, banner: null,
    ...over,
  };
}

describe("license banner", () => {
  it("is hidden for a valid non-trial license", () => {
    expect(bannerFor(view({}), "en")).toBeNull();
  });
  it("warns during grace and links to the License page", () => {
    const b = bannerFor(view({ state: "grace" }), "en");
    expect(b?.level).toBe("warning");
    expect(b?.cta?.href).toBe("/settings/license");
  });
  it("offers the trial to an unlicensed firm without a key", () => {
    const b = bannerFor(view({ state: "unlicensed", core_on: false, has_key: false }), "en");
    expect(b?.level).toBe("error");
    expect(b?.cta?.href).toContain("trial=1");
    expect(b?.cta?.labelKey).toBe("license.cta.start_trial");
  });
  it("adds contact support after a suspension or block", () => {
    const b = bannerFor(view({ state: "unlicensed", core_on: false, hub_error: "instance_blocked" }), "en");
    expect(b?.text).toContain("Contact support");
  });
  it("shows trial days left as info", () => {
    const b = bannerFor(view({ trial: true, trial_days_left: 3 }), "de");
    expect(b?.level).toBe("info");
    expect(b?.text).toContain("3");
  });
  it("sorts unseen support messages first and never alters their text", () => {
    const html = "Your plan ends <b>soon</b>";
    const v = view({ messages: [{ id: "a", level: "info", text: "seen one", seen: true }, { id: "b", level: "warning", text: html, seen: false }] });
    const list = unseenMessages(v);
    expect(list[0].id).toBe("b");
    expect(list[0].text).toBe(html);
  });
  it("renders entitlements with unlimited as infinity", () => {
    const rows = Object.fromEntries(entitlementRows(view({})).map((r) => [r.key, r.value]));
    expect(rows.max_client_companies).toBe("∞");
    expect(rows.max_documents_per_month).toBe("500");
    expect(rows.channels).toBe("✓");
    expect(rows.api).toBe("–");
    expect(expiresLabel(view({ expires_at: null }), "en")).toBe("never");
  });
});

describe("core tool not included (spec §8.4)", () => {
  it("names the missing tool instead of an ended license", () => {
    const v = view({ state: "expired", reason: "core_not_included", core_on: false });
    expect(stateKey(v)).toBe("license.state.core_not_included");
    const b = bannerFor(v, "en");
    expect(b?.level).toBe("error");
    expect(b?.text).toContain("does not include InvoiceFlow");
    expect(b?.cta?.href).toBe(v.store_links.buy);
  });
  it("keeps the ended wording when the core item ran out", () => {
    const v = view({ state: "expired", reason: "core_ended", core_on: false });
    expect(stateKey(v)).toBe("license.state.expired");
    expect(bannerFor(v, "en")?.text).toContain("has ended");
  });
});

describe("check now feedback", () => {
  it("confirms a successful check", () => {
    expect(refreshFlash({ ok: true, code: "ok", message: "x", retry_at: null }, "en")).toEqual({ tone: "success", text: "License checked with the hub." });
  });
  it("explains the 15-minute rule with the next possible time", () => {
    const f = refreshFlash({ ok: false, code: "too_soon", message: "The license was checked less than 15 minutes ago.", retry_at: 1800000900 }, "en");
    expect(f.tone).toBe("warning");
    expect(f.text).toContain("less than 15 minutes ago");
    expect(f.text).toMatch(/next check is possible at \d/);
  });
  it("shows the hub's message alone when no retry time is known", () => {
    expect(refreshFlash({ ok: false, code: "no_key", message: "Enter a license key first.", retry_at: null }, "de").text).toBe("Enter a license key first.");
  });
});
