// Pure helpers for the bank reconciliation page (unit-tested).
import type { BankTransaction, PaymentMatch } from "@/lib/api";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

/** Badge tone for a match score: ≥ 0.95 strong, ≥ 0.7 likely, otherwise weak. */
export function scoreTone(score: number): Tone {
  if (score >= 0.95) return "success";
  if (score >= 0.7) return "warning";
  return "neutral";
}

export function matchStatusTone(status: BankTransaction["match_status"]): Tone {
  switch (status) {
    case "matched":
      return "success";
    case "suggested":
      return "warning";
    case "ignored":
      return "neutral";
    default:
      return "info";
  }
}

/** The best open suggestion of a transaction (highest score first; the API already sorts). */
export function bestSuggestion(tx: BankTransaction): PaymentMatch | null {
  const open = tx.matches.filter((m) => m.status === "suggested");
  if (open.length === 0) return null;
  return open.reduce((a, b) => (b.score > a.score ? b : a));
}

/** Signed amount formatted with the sign kept visible (debits negative). */
export function signedAmount(amount: string, formatter: (value: string) => string): string {
  const text = formatter(amount.replace("-", ""));
  return amount.startsWith("-") ? `−${text}` : `+${text}`;
}

/** Validate an IBAN roughly (length and mod-97 checksum) for the manual account form. */
export function ibanLooksValid(raw: string): boolean {
  const iban = raw.replace(/\s+/g, "").toUpperCase();
  if (!/^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$/.test(iban)) return false;
  const rearranged = iban.slice(4) + iban.slice(0, 4);
  let remainder = 0;
  for (const ch of rearranged) {
    const value = ch >= "A" && ch <= "Z" ? String(ch.charCodeAt(0) - 55) : ch;
    for (const digit of value) remainder = (remainder * 10 + Number(digit)) % 97;
  }
  return remainder === 1;
}
