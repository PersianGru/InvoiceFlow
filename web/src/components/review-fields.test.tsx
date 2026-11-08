import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { t as translate, type MessageKey } from "@/i18n";
import type { Invoice } from "@/lib/api";
import { ReviewFields, getPath, setPath } from "./review-fields";

const invoice: Invoice = {
  document_type: "invoice", direction: "purchase", number: "INV-1", issue_date: "2026-10-01", delivery_date: null, currency: "EUR", language: "en",
  supplier: { name: "Acme", vat_id: "DE811234567" }, buyer: {}, lines: [], vat_breakdown: [], totals: { gross_total: "100.00" }, payment: {}, references: {}, notes: [],
  meta: {
    number: { confidence: 0.92, source: "ocr", location: { page: 1, x0: 0.1, y0: 0.1, x1: 0.2, y1: 0.12 }, ai_generated: true },
    "supplier.vat_id": { confidence: 1, source: "structured", location: null, ai_generated: false },
    "totals.gross_total": { confidence: 0.55, source: "llm_local", location: null, ai_generated: true },
  },
  validation: [], review_status: "needs_review",
};
const t = (k: MessageKey, v?: Record<string, string | number>) => translate("en", k, v);

describe("review fields", () => {
  it("marks AI-generated values and exact e-invoice values differently", () => {
    render(<ReviewFields invoice={invoice} draft={invoice as unknown as Record<string, unknown>} corrections={{}} onChange={() => {}} onFocusField={() => {}} focused={null} t={t} readOnly={false} />);
    expect(screen.getByText("92%")).toBeInTheDocument();
    expect(screen.getByText("XML")).toBeInTheDocument();
    expect(screen.getAllByText(/AI-generated, not yet confirmed/).length).toBeGreaterThan(0);
    expect(screen.getByText("55%")).toBeInTheDocument();
  });

  it("reports corrections by dotted path", () => {
    const onChange = vi.fn();
    render(<ReviewFields invoice={invoice} draft={invoice as unknown as Record<string, unknown>} corrections={{}} onChange={onChange} onFocusField={() => {}} focused={null} t={t} readOnly={false} />);
    fireEvent.change(screen.getByLabelText("Number"), { target: { value: "INV-2" } });
    expect(onChange).toHaveBeenCalledWith("number", "INV-2");
  });

  it("path helpers read and write nested values without mutating the original", () => {
    const base = { supplier: { name: "A" }, totals: { gross_total: "1" } };
    const next = setPath(base, "supplier.vat_id", "DE1");
    expect(getPath(next, "supplier.vat_id")).toBe("DE1");
    expect(getPath(base, "supplier.vat_id")).toBeUndefined();
    expect(getPath(next, "totals.gross_total")).toBe("1");
  });
});
