"use client";

import { fmtMs, fmtNumber, fmtPercent } from "@/lib/format";
import { OTHER, SERIES } from "@/lib/palette";
import { S } from "@/lib/strings";
import type { Span } from "@/lib/types";

import { Segmented } from "./ui";

export type Window = "1h" | "6h" | "24h" | "7d";

export function WindowPicker({ value, onChange }: { value: Window; onChange: (w: Window) => void }) {
  return (
    <Segmented
      label={S.system.window}
      value={value}
      onChange={onChange}
      options={(["1h", "6h", "24h", "7d"] as Window[]).map((w) => ({ id: w, label: S.system.windows[w] }))}
    />
  );
}

/** Parts of a whole: one bar, slots in fixed order, a legend with values (identity is never colour alone). */
export function ShareBar({ items, format = fmtNumber, label }: { items: { label: string; value: number }[]; format?: (v: number) => string; label: string }) {
  const sorted = items.filter((i) => i.value > 0);
  const shown = sorted.length > 8 ? [...sorted.slice(0, 7), { label: "Other", value: sorted.slice(7).reduce((n, i) => n + i.value, 0) }] : sorted;
  const total = shown.reduce((n, i) => n + i.value, 0) || 1;
  const colour = (i: number, name: string) => (name === "Other" ? OTHER : SERIES[i] ?? OTHER);
  return (
    <div className="stack tight">
      <div className="share-bar" role="img" aria-label={`${label}: ${shown.map((i) => `${i.label} ${format(i.value)}`).join(", ")}`}>
        {shown.map((item, i) => (
          <span key={item.label} style={{ width: `${(item.value / total) * 100}%`, background: colour(i, item.label) }} title={`${item.label}: ${format(item.value)}`} />
        ))}
      </div>
      <ul className="legend">
        {shown.map((item, i) => (
          <li key={item.label}>
            <span className="swatch" style={{ background: colour(i, item.label) }} />
            {item.label} <span className="num muted">{format(item.value)}</span>
            <span className="muted">({fmtPercent(item.value / total)})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A trace's spans laid out on its own time axis. */
export function Waterfall({ spans, total }: { spans: Span[]; total: number }) {
  const end = Math.max(total, ...spans.map((s) => s.start_ms + s.ms), 1);
  return (
    <div className="waterfall" role="table" aria-label={S.system.whereTime}>
      {spans.map((s, i) => (
        <div key={`${s.name}-${i}`} className="waterfall-row" role="row">
          <span role="cell" className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={s.name}>
            {s.name}
          </span>
          <span role="cell" className="waterfall-track">
            <span style={{ left: `${(s.start_ms / end) * 100}%`, width: `${(s.ms / end) * 100}%` }} />
          </span>
          <span role="cell" className="num" style={{ textAlign: "right" }}>
            {fmtMs(s.ms)}
          </span>
        </div>
      ))}
    </div>
  );
}
