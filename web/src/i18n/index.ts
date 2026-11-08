// i18n from day one: every UI string goes through `t()`. Launch locales: EN, DE, FR, NL, ES, IT, PL.
// English is the complete reference; other locales fall back to English per key until translated.
import de from "./messages/de.json";
import en from "./messages/en.json";
import es from "./messages/es.json";
import fr from "./messages/fr.json";
import it from "./messages/it.json";
import nl from "./messages/nl.json";
import pl from "./messages/pl.json";

export const LOCALES = ["en", "de", "fr", "nl", "es", "it", "pl"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_NAMES: Record<Locale, string> = { en: "English", de: "Deutsch", fr: "Français", nl: "Nederlands", es: "Español", it: "Italiano", pl: "Polski" };

type Messages = typeof en;
export type MessageKey = keyof Messages;

const MESSAGES: Record<Locale, Partial<Record<MessageKey, string>>> = { en, de, fr, nl, es, it, pl };

export function isLocale(value: string | undefined | null): value is Locale {
  return (LOCALES as readonly string[]).includes(value ?? "");
}

/** Translate `key` for `locale`, falling back to English, then to the key itself. */
export function t(locale: Locale, key: MessageKey, vars: Record<string, string | number> = {}): string {
  const text = MESSAGES[locale][key] ?? en[key] ?? key;
  return Object.entries(vars).reduce((s, [k, v]) => s.replaceAll(`{${k}}`, String(v)), text);
}

export function formatDate(locale: Locale, value: string | number | Date | null | undefined, style: "short" | "medium" | "long" = "medium"): string {
  if (value === null || value === undefined || value === "") return "—";
  const d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat(locale, { dateStyle: style }).format(d);
}

export function formatDateTime(locale: Locale, value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(d);
}

export function formatMoney(locale: Locale, amount: string | number | null | undefined, currency: string | null | undefined): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const n = typeof amount === "number" ? amount : Number(amount);
  if (Number.isNaN(n)) return String(amount);
  if (!currency) return new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency: currency.toUpperCase() }).format(n);
  } catch {
    return `${new Intl.NumberFormat(locale, { minimumFractionDigits: 2 }).format(n)} ${currency}`;
  }
}

export function formatPercent(locale: Locale, ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return "—";
  return new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1 }).format(ratio);
}
