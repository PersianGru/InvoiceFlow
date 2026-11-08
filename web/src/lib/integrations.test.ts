import { describe, expect, it } from "vitest";

import { addColumn, formatBytes, moveColumn, removeColumn, templatesFor } from "./integrations";

const cols = [
  { key: "number", header: "Nr" },
  { key: "issue_date", header: "Date" },
  { key: "totals.gross_total", header: "Gross" },
];

describe("template columns", () => {
  it("moves within bounds and leaves the input untouched", () => {
    expect(moveColumn(cols, 2, -1).map((c) => c.key)).toEqual(["number", "totals.gross_total", "issue_date"]);
    expect(moveColumn(cols, 0, -1)).toBe(cols);
    expect(moveColumn(cols, 2, 1)).toBe(cols);
    expect(cols[1].key).toBe("issue_date");
  });
  it("adds a column once with the registry label and removes by key", () => {
    const added = addColumn(cols, "currency", "Currency");
    expect(added).toHaveLength(4);
    expect(added[3]).toEqual({ key: "currency", header: "Currency" });
    expect(addColumn(added, "currency", "Currency")).toBe(added);
    expect(removeColumn(added, "number").map((c) => c.key)).toEqual(["issue_date", "totals.gross_total", "currency"]);
  });
});

describe("templatesFor", () => {
  const templates = [
    { id: "a", format: "csv", client_company_id: null },
    { id: "b", format: "csv", client_company_id: "c1" },
    { id: "c", format: "xlsx", client_company_id: null },
  ];
  it("offers only tabular templates matching the format and client", () => {
    expect(templatesFor(templates, "csv", "c1").map((t) => t.id)).toEqual(["a", "b"]);
    expect(templatesFor(templates, "csv", "c2").map((t) => t.id)).toEqual(["a"]);
    expect(templatesFor(templates, "csv", "").map((t) => t.id)).toEqual(["a", "b"]);
    expect(templatesFor(templates, "ubl", "c1")).toEqual([]);
  });
});

describe("formatBytes", () => {
  it("picks a readable unit", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
