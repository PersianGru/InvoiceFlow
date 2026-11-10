import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { t as translate, type MessageKey } from "@/i18n";
import { AllowancesEditor, LinesEditor, normaliseCell } from "./lines-editor";

const t = (k: MessageKey) => translate("en", k);
const LINES = [
  { description: "Karameliniai saldainiai", quantity: "1668.00", unit_price: "3.16", net_amount: null },
  { description: "Vienkartinis maišelis", net_amount: "0.01" },
];

describe("lines editor", () => {
  it("normalises decimal commas in number cells only", () => {
    expect(normaliseCell("quantity", "1,668")).toBe("1.668");
    expect(normaliseCell("net_amount", "1.234,56")).toBe("1234.56");
    expect(normaliseCell("unit_price", " 5.49 ")).toBe("5.49");
    expect(normaliseCell("description", "Saldainiai, ROSHEN")).toBe("Saldainiai, ROSHEN");
    expect(normaliseCell("quantity", "")).toBeNull();
  });

  it("edits a cell, removes and adds lines", () => {
    const onChange = vi.fn();
    render(<LinesEditor lines={LINES} onChange={onChange} readOnly={false} t={t} />);
    const qty = screen.getByLabelText("Qty 1");
    fireEvent.change(qty, { target: { value: "1,668" } });
    fireEvent.blur(qty);
    expect(onChange).toHaveBeenLastCalledWith([{ ...LINES[0], quantity: "1.668" }, LINES[1]]);
    fireEvent.click(screen.getByLabelText("Remove 2"));
    expect(onChange).toHaveBeenLastCalledWith([LINES[0]]);
    fireEvent.click(screen.getByText("Add line"));
    expect(onChange).toHaveBeenLastCalledWith([...LINES, { description: null }]);
  });

  it("edits document discounts and charges", () => {
    const onChange = vi.fn();
    const rows = [{ charge: false, amount: "10.00", reason: "Rabatt 5 %" }];
    render(<AllowancesEditor rows={rows} onChange={onChange} readOnly={false} t={t} />);
    fireEvent.change(screen.getByLabelText("Type 1"), { target: { value: "charge" } });
    expect(onChange).toHaveBeenLastCalledWith([{ ...rows[0], charge: true }]);
    const amount = screen.getByLabelText("Amount 1");
    fireEvent.change(amount, { target: { value: "12,50" } });
    fireEvent.blur(amount);
    expect(onChange).toHaveBeenLastCalledWith([{ ...rows[0], amount: "12.50" }]);
    fireEvent.click(screen.getByText("Add discount or charge"));
    expect(onChange).toHaveBeenLastCalledWith([...rows, { charge: false, amount: null }]);
  });

  it("shows plain values without inputs when read-only", () => {
    render(<LinesEditor lines={LINES} onChange={() => {}} readOnly t={t} />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText("Vienkartinis maišelis")).toBeInTheDocument();
  });
});
