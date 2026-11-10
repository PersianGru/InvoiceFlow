"use client";
// Editable tables of the review screen: line items, and the discounts and charges on the whole document.
// Each table is sent as one correction ("lines", "allowances_charges"); numbers typed with a decimal
// comma are sent with a dot.
import type { MessageKey } from "@/i18n";

export type Line = Record<string, unknown>;

type Column = { key: string; label: MessageKey; numeric: boolean; width: string; kind?: "charge" };

const NUMERIC = new Set(["quantity", "unit_price", "allowance_amount", "allowance_percent", "charge_amount", "net_amount", "vat_rate", "amount", "base_amount", "percent"]);
export const LINE_COLUMNS: Column[] = [
  { key: "description", label: "field.line.description", numeric: false, width: "min-w-48" },
  { key: "quantity", label: "field.line.quantity", numeric: true, width: "w-20" },
  { key: "unit", label: "field.line.unit", numeric: false, width: "w-14" },
  { key: "unit_price", label: "field.line.unit_price", numeric: true, width: "w-20" },
  { key: "allowance_amount", label: "field.line.allowance_amount", numeric: true, width: "w-20" },
  { key: "allowance_reason", label: "field.line.allowance_reason", numeric: false, width: "w-28" },
  { key: "charge_amount", label: "field.line.charge_amount", numeric: true, width: "w-20" },
  { key: "net_amount", label: "field.line.net_amount", numeric: true, width: "w-20" },
  { key: "vat_rate", label: "field.line.vat_rate", numeric: true, width: "w-16" },
  { key: "vat_category", label: "field.line.vat_category", numeric: false, width: "w-12" },
];
export const ALLOWANCE_COLUMNS: Column[] = [
  { key: "charge", label: "field.ac.kind", numeric: false, width: "w-28", kind: "charge" },
  { key: "reason", label: "field.ac.reason", numeric: false, width: "min-w-40" },
  { key: "percent", label: "field.ac.percent", numeric: true, width: "w-16" },
  { key: "base_amount", label: "field.ac.base_amount", numeric: true, width: "w-20" },
  { key: "amount", label: "field.ac.amount", numeric: true, width: "w-20" },
  { key: "vat_rate", label: "field.line.vat_rate", numeric: true, width: "w-16" },
  { key: "vat_category", label: "field.line.vat_category", numeric: false, width: "w-12" },
];

/** "1,668" → "1.668", "1.234,56" → "1234.56"; anything else unchanged; empty → null. */
export function normaliseCell(key: string, raw: string): string | null {
  const v = raw.trim();
  if (v === "") return null;
  if (!NUMERIC.has(key)) return v;
  let n = v.replace(/\s/g, "");
  if (n.includes(",") && n.includes(".")) n = n.lastIndexOf(",") > n.lastIndexOf(".") ? n.replace(/\./g, "").replace(",", ".") : n.replace(/,/g, "");
  else if (n.includes(",")) n = n.replace(",", ".");
  return n;
}

type Labels = { add: MessageKey; remove: MessageKey };

function RowsEditor({ rows, columns, blank, labels, onChange, readOnly, t }: { rows: Line[]; columns: Column[]; blank: Line; labels: Labels; onChange: (rows: Line[]) => void; readOnly: boolean; t: (k: MessageKey) => string }) {
  function setCell(i: number, key: string, value: unknown) {
    onChange(rows.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  }
  const show = (v: unknown, c: Column) => (c.kind === "charge" ? t(v === true ? "field.ac.charge" : "field.ac.discount") : v == null ? "" : String(v));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-neutral-600 dark:text-neutral-300">
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={`p-1 ${c.numeric ? "text-right" : ""}`}>
                {t(c.label)}
              </th>
            ))}
            {readOnly ? null : <th className="p-1" />}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={`${i}:${JSON.stringify(row)}`} className="border-t border-neutral-100 dark:border-neutral-800">
              {columns.map((c) => (
                <td key={c.key} className="p-1">
                  {readOnly ? (
                    <span className={c.numeric ? "block text-right tabular-nums" : ""}>{show(row[c.key], c)}</span>
                  ) : c.kind === "charge" ? (
                    <select aria-label={`${t(c.label)} ${i + 1}`} className={`${c.width} rounded border border-neutral-200 bg-transparent px-1 py-0.5 dark:border-neutral-700`} value={row[c.key] === true ? "charge" : "discount"} onChange={(e) => setCell(i, c.key, e.currentTarget.value === "charge")}>
                      <option value="discount">{t("field.ac.discount")}</option>
                      <option value="charge">{t("field.ac.charge")}</option>
                    </select>
                  ) : (
                    <input
                      aria-label={`${t(c.label)} ${i + 1}`}
                      className={`${c.width} w-full rounded border border-neutral-200 bg-transparent px-1 py-0.5 dark:border-neutral-700 ${c.numeric ? "text-right tabular-nums" : ""}`}
                      inputMode={c.numeric ? "decimal" : undefined}
                      defaultValue={row[c.key] == null ? "" : String(row[c.key])}
                      onBlur={(e) => {
                        const next = normaliseCell(c.key, e.currentTarget.value);
                        if (next !== (row[c.key] == null ? null : String(row[c.key]))) setCell(i, c.key, next);
                      }}
                    />
                  )}
                </td>
              ))}
              {readOnly ? null : (
                <td className="p-1 text-right">
                  <button type="button" className="text-xs text-red-700 underline dark:text-red-400" aria-label={`${t(labels.remove)} ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
                    {t("review.line_remove")}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {readOnly ? null : (
        <button type="button" className="mt-2 text-sm underline" onClick={() => onChange([...rows, blank])}>
          {t(labels.add)}
        </button>
      )}
    </div>
  );
}

export function LinesEditor({ lines, onChange, readOnly, t }: { lines: Line[]; onChange: (lines: Line[]) => void; readOnly: boolean; t: (k: MessageKey) => string }) {
  return <RowsEditor rows={lines} columns={LINE_COLUMNS} blank={{ description: null }} labels={{ add: "review.line_add", remove: "review.line_remove" }} onChange={onChange} readOnly={readOnly} t={t} />;
}

/** Discounts and charges on the whole document (e.g. 5 % off the total, freight). */
export function AllowancesEditor({ rows, onChange, readOnly, t }: { rows: Line[]; onChange: (rows: Line[]) => void; readOnly: boolean; t: (k: MessageKey) => string }) {
  return <RowsEditor rows={rows} columns={ALLOWANCE_COLUMNS} blank={{ charge: false, amount: null }} labels={{ add: "review.ac_add", remove: "review.ac_remove" }} onChange={onChange} readOnly={readOnly} t={t} />;
}
