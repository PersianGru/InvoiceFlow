import { describe, expect, it } from "vitest";

import type { BankTransaction, PaymentMatch } from "@/lib/api";
import { bestSuggestion, ibanLooksValid, matchStatusTone, scoreTone, signedAmount } from "@/lib/bank";

function match(over: Partial<PaymentMatch>): PaymentMatch {
  return {
    id: 1,
    transaction_id: "t",
    document_id: "d",
    amount: "1.00",
    score: 0.5,
    method: "auto",
    status: "suggested",
    reasons: [],
    invoice_number: null,
    supplier_name: null,
    gross_total: null,
    created_at: "",
    ...over,
  };
}

describe("bank helpers", () => {
  it("maps scores and statuses to tones", () => {
    expect(scoreTone(0.99)).toBe("success");
    expect(scoreTone(0.8)).toBe("warning");
    expect(scoreTone(0.5)).toBe("neutral");
    expect(matchStatusTone("matched")).toBe("success");
    expect(matchStatusTone("suggested")).toBe("warning");
    expect(matchStatusTone("unmatched")).toBe("info");
  });

  it("picks the best open suggestion and ignores decided ones", () => {
    const tx = {
      matches: [match({ id: 1, score: 0.6 }), match({ id: 2, score: 0.9 }), match({ id: 3, score: 1, status: "rejected" })],
    } as unknown as BankTransaction;
    expect(bestSuggestion(tx)?.id).toBe(2);
    expect(bestSuggestion({ matches: [] } as unknown as BankTransaction)).toBeNull();
  });

  it("keeps the sign visible on amounts", () => {
    expect(signedAmount("-12.50", (v) => `€${v}`)).toBe("−€12.50");
    expect(signedAmount("12.50", (v) => `€${v}`)).toBe("+€12.50");
  });

  it("checks IBANs with the mod-97 rule", () => {
    expect(ibanLooksValid("DE89 3704 0044 0532 0130 00")).toBe(true);
    expect(ibanLooksValid("DE89370400440532013001")).toBe(false);
    expect(ibanLooksValid("not an iban")).toBe(false);
  });
});
