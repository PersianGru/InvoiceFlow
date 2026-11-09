// Every EU member state (plus CH); the Baltic states first, then the rest alphabetically. Each has a
// VAT-rate entry, a retention period and (where one exists) an e-invoicing mandate in the rule packs.
export const COUNTRIES = ["EE", "LV", "LT", "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LU", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"];

// Localised country name from the browser (Intl), falling back to the code.
export function countryLabel(code: string, locale: string): string {
  try {
    const name = new Intl.DisplayNames([locale], { type: "region" }).of(code);
    return name && name !== code ? `${code} · ${name}` : code;
  } catch {
    return code;
  }
}

/** The token of a one-time link: it travels in the URL fragment (`#t=…`), never in the query. */
export function fragmentToken(hash: string): string | null {
  const m = /(?:^#|&)t=([A-Za-z0-9_-]{10,128})/.exec(hash);
  return m ? m[1] : null;
}

/** Where a signed-in user goes first. */
export function homeFor(user: { role: string; mfa_setup_required?: boolean; acting_firm_id?: string | null }): string {
  if (user.mfa_setup_required) return "/setup-2fa";
  if (user.role === "client_admin" || user.role === "client_user") return "/my";
  if (user.role === "super_admin" && !user.acting_firm_id) return "/platform";
  return "/inbox";
}
