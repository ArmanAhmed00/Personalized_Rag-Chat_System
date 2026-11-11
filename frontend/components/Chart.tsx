"use client";

// Hand-written SVG charts for the specs the server validated: bar, line, scatter, histogram and
// heatmap. Thin marks, one value axis, a recessive grid, a legend for two or more series, a hover
// (and keyboard) tooltip, and a table view with CSV download for every chart.

import { useMemo, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";

import { saveBlob } from "@/lib/api";
import { fmtCell, fmtCompact, fmtNumber, toCsv } from "@/lib/format";
import { useWidth } from "@/lib/hooks";
import { DIVERGING_CSS, SEQUENTIAL_CSS, SURFACE, diverging, inkOn, seriesColour, sequential } from "@/lib/palette";
import { S, t } from "@/lib/strings";
import type { ChartSpec } from "@/lib/types";

import { Segmented } from "./ui";

type Row = Record<string, unknown>;

interface Tip {
  x: number;
  y: number;
  title: string;
  rows: { colour?: string; label: string; value: string }[];
}

interface Drawn {
  svg: ReactNode;
  height: number;
  legend: { label: string; colour: string }[];
  heat?: { lo: number; hi: number; diverging: boolean };
  /** Positions the keyboard can step through (bar, line, histogram). */
  steps: number;
  tipAt: (index: number) => Tip | null;
  hover: (x: number, y: number) => { tip: Tip | null; index: number | null };
}

const FONT_W = 6.4; // average width of an 11px label character
const BAR_MAX = 24;
const GAP = 2;
const RADIUS = 4;

// ------------------------------------------------------------------ scales

function niceStep(span: number, count: number): number {
  const raw = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const norm = raw / mag;
  return (norm >= 7.5 ? 10 : norm >= 3.5 ? 5 : norm >= 1.5 ? 2 : 1) * mag;
}

function niceDomain(lo: number, hi: number, count = 5): { lo: number; hi: number; ticks: number[] } {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { lo: 0, hi: 1, ticks: [0, 1] };
  if (lo === hi) {
    const pad = Math.abs(lo) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  }
  const step = niceStep(hi - lo, count);
  const a = Math.floor(lo / step) * step;
  const b = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = a; v <= b + step / 2; v += step) ticks.push(Math.abs(v) < step / 1e6 ? 0 : v);
  return { lo: a, hi: b, ticks };
}

function linear(d0: number, d1: number, r0: number, r1: number) {
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  return (v: number) => r0 + (v - d0) * k;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function label(v: unknown): string {
  return v === null || v === undefined ? "—" : String(v);
}

function short(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, Math.max(1, max - 1))}…` : text;
}

function unique(values: unknown[]): unknown[] {
  const seen = new Set<string>();
  const out: unknown[] = [];
  for (const v of values) {
    const key = JSON.stringify(v);
    if (!seen.has(key)) {
      seen.add(key);
      out.push(v);
    }
  }
  return out;
}

/** A bar with a 4px rounded data end, anchored square to the baseline. */
function barPath(vertical: boolean, at: number, size: number, base: number, end: number): string {
  const length = Math.abs(end - base);
  if (length < 0.5) return "";
  const r = Math.min(RADIUS, size / 2, length);
  if (vertical) {
    const d = end < base ? 1 : -1; // up: +1
    const x = at;
    const w = size;
    return `M${x},${base}V${end + d * r}Q${x},${end} ${x + r},${end}H${x + w - r}Q${x + w},${end} ${x + w},${end + d * r}V${base}Z`;
  }
  const d = end > base ? 1 : -1; // right: +1
  const y = at;
  const h = size;
  return `M${base},${y}H${end - d * r}Q${end},${y} ${end},${y + r}V${y + h - r}Q${end},${y + h} ${end - d * r},${y + h}H${base}Z`;
}

function seriesOf(spec: ChartSpec): unknown[] {
  return spec.series ? unique(spec.data.map((r) => r[spec.series as string])) : [null];
}

function colours(names: unknown[]): string[] {
  let slot = 0;
  return names.map((n) => (n === "Other" ? seriesColour(0, "Other") : seriesColour(slot++)));
}

// ------------------------------------------------------------------ axes

function YAxis({ ticks, y, left, right, fmt }: { ticks: number[]; y: (v: number) => number; left: number; right: number; fmt: (v: number) => string }) {
  return (
    <g>
      <g className="grid">
        {ticks.map((t) => (
          <line key={t} x1={left} x2={right} y1={y(t)} y2={y(t)} />
        ))}
      </g>
      {ticks.map((t) => (
        <text key={t} x={left - 8} y={y(t)} dy="0.32em" textAnchor="end">
          {fmt(t)}
        </text>
      ))}
    </g>
  );
}

function XGrid({ ticks, x, top, bottom, fmt }: { ticks: number[]; x: (v: number) => number; top: number; bottom: number; fmt: (v: number) => string }) {
  return (
    <g>
      <g className="grid">
        {ticks.map((t) => (
          <line key={t} y1={top} y2={bottom} x1={x(t)} x2={x(t)} />
        ))}
      </g>
      {ticks.map((t) => (
        <text key={t} x={x(t)} y={bottom + 16} textAnchor="middle">
          {fmt(t)}
        </text>
      ))}
    </g>
  );
}

function CategoryLabels({ labels, at, y, band }: { labels: string[]; at: (i: number) => number; y: number; band: number }) {
  const longest = Math.min(14, Math.max(...labels.map((l) => l.length), 1));
  const every = Math.max(1, Math.ceil((longest * FONT_W + 8) / Math.max(band, 1)));
  return (
    <g>
      {labels.map((l, i) =>
        i % every === 0 ? (
          <text key={i} x={at(i)} y={y} textAnchor="middle">
            <title>{l}</title>
            {short(l, 14)}
          </text>
        ) : null,
      )}
    </g>
  );
}

function AxisTitles({ spec, width, height, top, bottom }: { spec: ChartSpec; width: number; height: number; top: number; bottom: number }) {
  return (
    <g>
      {spec.y_label ? (
        <text x={0} y={Math.max(10, top - 12)} textAnchor="start">
          {short(spec.y_label, 60)}
        </text>
      ) : null}
      {spec.x_label ? (
        <text x={width / 2} y={Math.min(height - 4, bottom + 34)} textAnchor="middle">
          {short(spec.x_label, 60)}
        </text>
      ) : null}
    </g>
  );
}

// ------------------------------------------------------------------ chart kinds

function drawBar(spec: ChartSpec, width: number, active: number | null): Drawn {
  const cats = unique(spec.data.map((r) => r[spec.x]));
  const names = seriesOf(spec);
  const fills = colours(names);
  const value = new Map<string, number | null>();
  for (const r of spec.data) value.set(JSON.stringify([r[spec.x], spec.series ? r[spec.series] : null]), num(r[spec.y]));
  const get = (c: unknown, s: unknown) => value.get(JSON.stringify([c, s])) ?? null;
  const all = [...value.values()].filter((v): v is number => v !== null);
  const dom = niceDomain(Math.min(0, ...all), Math.max(0, ...all));
  const labels = cats.map(label);
  const longest = Math.max(...labels.map((l) => l.length), 1);
  const horizontal = spec.x_kind === "category" && (longest > 10 || cats.length > 12) && width > 0;
  const n = names.length;
  const fmt = (v: number) => fmtCompact(v);

  const tipFor = (i: number, x: number, y: number): Tip => ({
    x,
    y,
    title: labels[i] ?? "",
    rows: names.map((s, k) => ({ colour: fills[k], label: s === null ? spec.y : label(s), value: fmtNumber(get(cats[i], s)) })),
  });

  if (horizontal) {
    const left = Math.min(180, Math.max(60, Math.min(longest, 24) * FONT_W + 14));
    const right = width - 16;
    const top = spec.y_label ? 28 : 12;
    const groupH = Math.min(n * BAR_MAX + (n - 1) * GAP, 64);
    const barH = Math.min(BAR_MAX, (groupH - (n - 1) * GAP) / n);
    const band = Math.max(groupH + 10, 22);
    const bottom = top + band * cats.length;
    const height = bottom + 40;
    const x = linear(dom.lo, dom.hi, left, right);
    const base = x(0);
    const centre = (i: number) => top + band * i + band / 2;
    const svg = (
      <>
        <XGrid ticks={dom.ticks} x={x} top={top} bottom={bottom} fmt={fmt} />
        <line className="axis-line" x1={base} x2={base} y1={top} y2={bottom} />
        {labels.map((l, i) => (
          <text key={i} x={left - 8} y={centre(i)} dy="0.32em" textAnchor="end" fontWeight={active === i ? 600 : undefined}>
            <title>{l}</title>
            {short(l, 24)}
          </text>
        ))}
        {cats.map((c, i) =>
          names.map((s, k) => {
            const v = get(c, s);
            if (v === null) return null;
            const yy = centre(i) - groupH / 2 + k * (barH + GAP);
            return <path key={`${i}-${k}`} d={barPath(false, yy, barH, base, x(v))} fill={fills[k]} opacity={active === null || active === i ? 1 : 0.45} />;
          }),
        )}
        {cats.map((_, i) => (
          <rect key={i} className="hit" x={left} width={right - left} y={top + band * i} height={band} />
        ))}
        <AxisTitles spec={spec} width={width} height={height} top={top} bottom={bottom} />
      </>
    );
    return {
      svg,
      height,
      legend: n > 1 ? names.map((s, k) => ({ label: label(s), colour: fills[k] ?? "" })) : [],
      steps: cats.length,
      tipAt: (i) => tipFor(i, (left + right) / 2, centre(i) - groupH / 2),
      hover: (_px, py) => {
        const i = Math.floor((py - top) / band);
        if (i < 0 || i >= cats.length) return { tip: null, index: null };
        return { tip: tipFor(i, (left + right) / 2, centre(i) - groupH / 2), index: i };
      },
    };
  }

  const height = 280;
  const left = Math.max(40, Math.max(...dom.ticks.map((t) => fmt(t).length)) * FONT_W + 14);
  const right = width - 12;
  const top = spec.y_label ? 30 : 14;
  const bottom = height - (spec.x_label ? 46 : 30);
  const y = linear(dom.lo, dom.hi, bottom, top);
  const band = (right - left) / Math.max(1, cats.length);
  const groupW = Math.min(band * 0.78, n * BAR_MAX + (n - 1) * GAP);
  const barW = Math.max(1, (groupW - (n - 1) * GAP) / n);
  const centre = (i: number) => left + band * i + band / 2;
  const base = y(0);
  const svg = (
    <>
      <YAxis ticks={dom.ticks} y={y} left={left} right={right} fmt={fmt} />
      {cats.map((c, i) =>
        names.map((s, k) => {
          const v = get(c, s);
          if (v === null) return null;
          const xx = centre(i) - groupW / 2 + k * (barW + GAP);
          return <path key={`${i}-${k}`} d={barPath(true, xx, barW, base, y(v))} fill={fills[k]} opacity={active === null || active === i ? 1 : 0.45} />;
        }),
      )}
      <line className="axis-line" x1={left} x2={right} y1={base} y2={base} />
      <CategoryLabels labels={labels} at={centre} y={bottom + 16} band={band} />
      {cats.map((_, i) => (
        <rect key={i} className="hit" x={left + band * i} width={band} y={top} height={bottom - top} />
      ))}
      <AxisTitles spec={spec} width={width} height={height} top={top} bottom={bottom} />
    </>
  );
  const tipY = (i: number) => {
    const vals = names.map((s) => get(cats[i], s)).filter((v): v is number => v !== null);
    return Math.min(...vals.map((v) => y(v)), base);
  };
  return {
    svg,
    height,
    legend: n > 1 ? names.map((s, k) => ({ label: label(s), colour: fills[k] ?? "" })) : [],
    steps: cats.length,
    tipAt: (i) => tipFor(i, centre(i), tipY(i)),
    hover: (px) => {
      const i = Math.floor((px - left) / band);
      if (i < 0 || i >= cats.length) return { tip: null, index: null };
      return { tip: tipFor(i, centre(i), tipY(i)), index: i };
    },
  };
}

function drawLine(spec: ChartSpec, width: number, active: number | null): Drawn {
  const names = seriesOf(spec);
  const fills = colours(names);
  const numeric = spec.x_kind === "number";
  const cats = unique(spec.data.map((r) => r[spec.x]));
  if (numeric) cats.sort((a, b) => (num(a) ?? 0) - (num(b) ?? 0));
  const labels = cats.map(label);
  const value = new Map<string, number | null>();
  for (const r of spec.data) value.set(JSON.stringify([r[spec.x], spec.series ? r[spec.series] : null]), num(r[spec.y]));
  const get = (c: unknown, s: unknown) => value.get(JSON.stringify([c, s])) ?? null;
  const all = [...value.values()].filter((v): v is number => v !== null);
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const dom = niceDomain(lo >= 0 && lo < (hi - lo) * 0.6 ? 0 : lo, hi);
  const fmt = (v: number) => fmtCompact(v);

  const direct = names.length >= 2 && names.length <= 4;
  const endLabelW = direct ? Math.min(110, Math.max(...names.map((s) => label(s).length)) * FONT_W + 22) : 0;
  const height = 280;
  const left = Math.max(40, Math.max(...dom.ticks.map((t) => fmt(t).length)) * FONT_W + 14);
  const right = width - 14 - endLabelW;
  const top = spec.y_label ? 30 : 14;
  const bottom = height - (spec.x_label ? 46 : 30);
  const y = linear(dom.lo, dom.hi, bottom, top);
  const xs = numeric
    ? (() => {
        const v = cats.map((c) => num(c) ?? 0);
        const xd = niceDomain(Math.min(...v), Math.max(...v));
        const f = linear(Math.min(...v), Math.max(...v), left, right);
        return { at: (i: number) => f(v[i] ?? 0), ticks: xd.ticks.filter((t) => t >= Math.min(...v) && t <= Math.max(...v)), scale: f };
      })()
    : { at: (i: number) => (cats.length === 1 ? (left + right) / 2 : left + ((right - left) * i) / (cats.length - 1)), ticks: [] as number[], scale: null };
  const band = cats.length > 1 ? (right - left) / (cats.length - 1) : right - left;

  const paths = names.map((s) => {
    let d = "";
    let pen = false;
    cats.forEach((c, i) => {
      const v = get(c, s);
      if (v === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${xs.at(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  });

  const markers = cats.length <= 24;
  // Direct labels at the end of each line, nudged apart so they don't collide.
  const ends = direct
    ? names
        .map((s, k) => {
          let last: number | null = null;
          for (let i = cats.length - 1; i >= 0 && last === null; i--) last = get(cats[i], s);
          return { k, text: label(s), y: last === null ? null : y(last) };
        })
        .filter((e): e is { k: number; text: string; y: number } => e.y !== null)
        .sort((a, b) => a.y - b.y)
    : [];
  for (let i = 1; i < ends.length; i++) {
    const prev = ends[i - 1]!;
    const cur = ends[i]!;
    if (cur.y - prev.y < 13) cur.y = prev.y + 13;
  }

  const tipFor = (i: number): Tip => {
    const vals = names.map((s) => get(cats[i], s)).filter((v): v is number => v !== null);
    return {
      x: xs.at(i),
      y: vals.length ? Math.min(...vals.map((v) => y(v))) : top,
      title: labels[i] ?? "",
      rows: names.map((s, k) => ({ colour: fills[k], label: s === null ? spec.y : label(s), value: fmtNumber(get(cats[i], s)) })),
    };
  };

  const svg = (
    <>
      <YAxis ticks={dom.ticks} y={y} left={left} right={right} fmt={fmt} />
      <line className="axis-line" x1={left} x2={right} y1={bottom} y2={bottom} />
      {numeric
        ? xs.ticks.map((t) => (
            <text key={t} x={xs.scale!(t)} y={bottom + 16} textAnchor="middle">
              {fmtCompact(t)}
            </text>
          ))
        : null}
      {!numeric ? <CategoryLabels labels={labels} at={xs.at} y={bottom + 16} band={band} /> : null}
      {active !== null ? <line className="crosshair" x1={xs.at(active)} x2={xs.at(active)} y1={top} y2={bottom} /> : null}
      {paths.map((d, k) => (
        <path key={k} d={d} fill="none" stroke={fills[k]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      ))}
      {names.map((s, k) =>
        cats.map((c, i) => {
          const v = get(c, s);
          if (v === null || (!markers && active !== i)) return null;
          return <circle key={`${k}-${i}`} cx={xs.at(i)} cy={y(v)} r={active === i ? 5 : 4} fill={fills[k]} stroke={SURFACE} strokeWidth={2} />;
        }),
      )}
      {ends.map((e) => (
        <text key={e.k} x={right + 10} y={e.y} dy="0.32em" style={{ fill: "var(--ink-2)" }}>
          {short(e.text, 14)}
        </text>
      ))}
      <rect className="hit" x={left - band / 2} width={right - left + band} y={top} height={bottom - top} />
      <AxisTitles spec={spec} width={width} height={height} top={top} bottom={bottom} />
    </>
  );
  const nearest = (px: number) => {
    let best = 0;
    let dist = Infinity;
    cats.forEach((_, i) => {
      const d = Math.abs(xs.at(i) - px);
      if (d < dist) {
        dist = d;
        best = i;
      }
    });
    return best;
  };
  return {
    svg,
    height,
    legend: names.length > 1 ? names.map((s, k) => ({ label: label(s), colour: fills[k] ?? "" })) : [],
    steps: cats.length,
    tipAt: (i) => tipFor(i),
    hover: (px) => {
      if (px < left - band / 2 || px > right + band / 2) return { tip: null, index: null };
      const i = nearest(px);
      return { tip: tipFor(i), index: i };
    },
  };
}

function drawHistogram(spec: ChartSpec, width: number, active: number | null): Drawn {
  const rows = spec.data;
  const from = rows.map((r) => num(r[spec.x]) ?? 0);
  const width0 = from.length > 1 ? (from[1]! - from[0]!) : 1;
  const to = rows.map((r, i) => num(r.to) ?? from[i + 1] ?? from[i]! + width0);
  const counts = rows.map((r) => num(r[spec.y]) ?? 0);
  const dom = niceDomain(0, Math.max(...counts, 1));
  const fmt = (v: number) => fmtCompact(v);
  const height = 260;
  const left = Math.max(40, Math.max(...dom.ticks.map((t) => fmt(t).length)) * FONT_W + 14);
  const right = width - 14;
  const top = spec.y_label ? 30 : 14;
  const bottom = height - (spec.x_label ? 46 : 30);
  const lo = Math.min(...from);
  const hi = Math.max(...to);
  const x = linear(lo, hi, left, right);
  const y = linear(dom.lo, dom.hi, bottom, top);
  const xt = niceDomain(lo, hi, Math.max(2, Math.floor((right - left) / 80))).ticks.filter((t) => t >= lo && t <= hi);
  const tipFor = (i: number): Tip => ({
    x: (x(from[i]!) + x(to[i]!)) / 2,
    y: y(counts[i]!),
    title: `${fmtNumber(from[i])} – ${fmtNumber(to[i])}`,
    rows: [{ colour: seriesColour(0), label: spec.y, value: fmtNumber(counts[i]) }],
  });
  const svg = (
    <>
      <YAxis ticks={dom.ticks} y={y} left={left} right={right} fmt={fmt} />
      {counts.map((c, i) => {
        const x0 = x(from[i]!) + GAP / 2;
        const w = Math.max(1, x(to[i]!) - x(from[i]!) - GAP);
        return <path key={i} d={barPath(true, x0, w, y(0), y(c))} fill={seriesColour(0)} opacity={active === null || active === i ? 1 : 0.45} />;
      })}
      <line className="axis-line" x1={left} x2={right} y1={y(0)} y2={y(0)} />
      {xt.map((t) => (
        <text key={t} x={x(t)} y={bottom + 16} textAnchor="middle">
          {fmtCompact(t)}
        </text>
      ))}
      <rect className="hit" x={left} width={right - left} y={top} height={bottom - top} />
      <AxisTitles spec={spec} width={width} height={height} top={top} bottom={bottom} />
    </>
  );
  return {
    svg,
    height,
    legend: [],
    steps: counts.length,
    tipAt: tipFor,
    hover: (px) => {
      const v = lo + ((px - left) / (right - left)) * (hi - lo);
      const i = from.findIndex((f, k) => v >= f && v < to[k]! + (k === from.length - 1 ? 1e-9 : 0));
      return i < 0 ? { tip: null, index: null } : { tip: tipFor(i), index: i };
    },
  };
}

function drawScatter(spec: ChartSpec, width: number, active: number | null): Drawn {
  const pts = spec.data
    .map((r) => ({ x: num(r[spec.x]), y: num(r[spec.y]), s: spec.series ? r[spec.series] : null, r }))
    .filter((p): p is { x: number; y: number; s: unknown; r: Row } => p.x !== null && p.y !== null);
  const names = seriesOf(spec);
  const fills = colours(names);
  const colourOf = new Map(names.map((n, k) => [JSON.stringify(n), fills[k] ?? ""]));
  const xd = niceDomain(Math.min(...pts.map((p) => p.x)), Math.max(...pts.map((p) => p.x)));
  const yd = niceDomain(Math.min(...pts.map((p) => p.y)), Math.max(...pts.map((p) => p.y)));
  const fmt = (v: number) => fmtCompact(v);
  const height = 300;
  const left = Math.max(40, Math.max(...yd.ticks.map((t) => fmt(t).length)) * FONT_W + 14);
  const right = width - 14;
  const top = spec.y_label ? 30 : 14;
  const bottom = height - (spec.x_label ? 46 : 30);
  const x = linear(xd.lo, xd.hi, left, right);
  const y = linear(yd.lo, yd.hi, bottom, top);
  const many = pts.length > 300;
  // "Other" draws first so the named groups sit on top.
  const order = pts.map((_, i) => i).sort((a, b) => Number(pts[b]!.s !== "Other") - Number(pts[a]!.s !== "Other")).reverse();
  const tipFor = (i: number): Tip => {
    const p = pts[i]!;
    const rows = [
      { label: spec.x_label || spec.x, value: fmtNumber(p.x) },
      { label: spec.y_label || spec.y, value: fmtNumber(p.y) },
    ];
    return {
      x: x(p.x),
      y: y(p.y),
      title: spec.series ? label(p.s) : `${spec.x} × ${spec.y}`,
      rows: spec.series ? [{ colour: colourOf.get(JSON.stringify(p.s)), label: spec.series, value: label(p.s) }, ...rows] : rows,
    };
  };
  const svg = (
    <>
      <YAxis ticks={yd.ticks} y={y} left={left} right={right} fmt={fmt} />
      <XGrid ticks={xd.ticks} x={x} top={top} bottom={bottom} fmt={fmt} />
      <line className="axis-line" x1={left} x2={right} y1={bottom} y2={bottom} />
      {order.map((i) => {
        const p = pts[i]!;
        return (
          <circle
            key={i}
            cx={x(p.x)}
            cy={y(p.y)}
            r={active === i ? 6 : 4}
            fill={colourOf.get(JSON.stringify(p.s)) ?? seriesColour(0)}
            fillOpacity={many ? 0.7 : 0.9}
            stroke={SURFACE}
            strokeWidth={many ? 1 : 2}
          />
        );
      })}
      <rect className="hit" x={left} width={right - left} y={top} height={bottom - top} />
      <AxisTitles spec={spec} width={width} height={height} top={top} bottom={bottom} />
    </>
  );
  return {
    svg,
    height,
    legend: names.length > 1 ? names.map((s, k) => ({ label: label(s), colour: fills[k] ?? "" })) : [],
    steps: 0,
    tipAt: () => null,
    hover: (px, py) => {
      let best = -1;
      let dist = 24 * 24;
      pts.forEach((p, i) => {
        const d = (x(p.x) - px) ** 2 + (y(p.y) - py) ** 2;
        if (d < dist) {
          dist = d;
          best = i;
        }
      });
      return best < 0 ? { tip: null, index: null } : { tip: tipFor(best), index: best };
    },
  };
}

function drawHeatmap(spec: ChartSpec, width: number, active: number | null): Drawn {
  const valueKey = spec.value as string;
  const cols = unique(spec.data.map((r) => r[spec.x]));
  const rows = unique(spec.data.map((r) => r[spec.y]));
  const cell = new Map<string, number | null>();
  for (const r of spec.data) cell.set(JSON.stringify([r[spec.x], r[spec.y]]), num(r[valueKey]));
  const vals = [...cell.values()].filter((v): v is number => v !== null);
  const lo = Math.min(...vals, 0);
  const hi = Math.max(...vals, 0);
  const isDiv = lo < 0;
  const extent = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
  const fill = (v: number | null) => {
    if (v === null) return "#eef1f6";
    return isDiv ? diverging(v / extent) : sequential(hi === lo ? 0.5 : (v - Math.min(...vals)) / (hi - Math.min(...vals) || 1));
  };
  const rowLabels = rows.map(label);
  const colLabels = cols.map(label);
  const left = Math.min(170, Math.max(...rowLabels.map((l) => Math.min(l.length, 24)), 1) * FONT_W + 14);
  const topLabelW = Math.max(...colLabels.map((l) => Math.min(l.length, 18)), 1) * FONT_W;
  const size = Math.max(14, Math.min(44, (width - left - 10) / Math.max(1, cols.length)));
  const rotate = topLabelW + 6 > size;
  const top = rotate ? Math.min(130, topLabelW * 0.72 + 16) : 24;
  const height = top + size * rows.length + 8;
  const showValues = size >= 34;
  const at = (c: number, r: number) => ({ x: left + c * size, y: top + r * size });
  const index = (c: number, r: number) => r * cols.length + c;
  const tipFor = (c: number, r: number): Tip => ({
    x: at(c, r).x + size / 2,
    y: at(c, r).y,
    title: `${rowLabels[r]} × ${colLabels[c]}`,
    rows: [{ label: valueKey, value: fmtNumber(cell.get(JSON.stringify([cols[c], rows[r]])) ?? null) }],
  });
  const svg = (
    <>
      {colLabels.map((l, c) =>
        rotate ? (
          <text key={c} transform={`translate(${left + c * size + size / 2 + 3},${top - 6}) rotate(-45)`} textAnchor="start">
            <title>{l}</title>
            {short(l, 18)}
          </text>
        ) : (
          <text key={c} x={left + c * size + size / 2} y={top - 8} textAnchor="middle">
            <title>{l}</title>
            {short(l, 10)}
          </text>
        ),
      )}
      {rowLabels.map((l, r) => (
        <text key={r} x={left - 8} y={top + r * size + size / 2} dy="0.32em" textAnchor="end">
          <title>{l}</title>
          {short(l, 24)}
        </text>
      ))}
      {rows.map((rv, r) =>
        cols.map((cv, c) => {
          const v = cell.get(JSON.stringify([cv, rv])) ?? null;
          const f = fill(v);
          const p = at(c, r);
          return (
            <g key={`${r}-${c}`}>
              <rect
                x={p.x + GAP / 2}
                y={p.y + GAP / 2}
                width={size - GAP}
                height={size - GAP}
                rx={3}
                fill={f}
                stroke={active === index(c, r) ? "#14213d" : "none"}
                strokeWidth={2}
              />
              {showValues && v !== null ? (
                <text x={p.x + size / 2} y={p.y + size / 2} dy="0.32em" textAnchor="middle" style={{ fill: inkOn(f), fontSize: 10 }}>
                  {Math.abs(v) < 10 && !Number.isInteger(v) ? v.toFixed(2) : fmtCompact(v)}
                </text>
              ) : null}
            </g>
          );
        }),
      )}
      <rect className="hit" x={left} y={top} width={size * cols.length} height={size * rows.length} />
    </>
  );
  return {
    svg,
    height,
    legend: [],
    heat: { lo: isDiv ? -extent : Math.min(...vals), hi: isDiv ? extent : hi, diverging: isDiv },
    steps: 0,
    tipAt: () => null,
    hover: (px, py) => {
      const c = Math.floor((px - left) / size);
      const r = Math.floor((py - top) / size);
      if (c < 0 || r < 0 || c >= cols.length || r >= rows.length) return { tip: null, index: null };
      return { tip: tipFor(c, r), index: index(c, r) };
    },
  };
}

function draw(spec: ChartSpec, width: number, active: number | null): Drawn {
  switch (spec.type) {
    case "line":
      return drawLine(spec, width, active);
    case "histogram":
      return drawHistogram(spec, width, active);
    case "scatter":
      return drawScatter(spec, width, active);
    case "heatmap":
      return drawHeatmap(spec, width, active);
    default:
      return drawBar(spec, width, active);
  }
}

// ------------------------------------------------------------------ the component

export function tableOf(spec: ChartSpec): { header: string[]; rows: unknown[][] } {
  const header = unique([spec.x, ...(spec.series ? [spec.series] : []), spec.y, ...(spec.value ? [spec.value] : []), ...Object.keys(spec.data[0] ?? {})]).map(String);
  return { header, rows: spec.data.map((r) => header.map((h) => r[h])) };
}

export function Chart({ spec, note, showTitle = true }: { spec: ChartSpec; note?: string | null; showTitle?: boolean }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const [ref, width] = useWidth<HTMLDivElement>(640);
  const [active, setActive] = useState<number | null>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const [keyboard, setKeyboard] = useState(false);
  const drawn = useMemo(() => draw(spec, Math.max(280, width), active), [spec, width, active]);
  const table = useMemo(() => tableOf(spec), [spec]);

  const onMove = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const scale = Math.max(280, width) / box.width;
    const found = drawn.hover((event.clientX - box.left) * scale, (event.clientY - box.top) * scale);
    setKeyboard(false);
    setTip(found.tip);
    setActive(found.index);
  };
  const clear = () => {
    setTip(null);
    setActive(null);
  };
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!drawn.steps) return;
    let next = active;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = active === null ? 0 : Math.min(drawn.steps - 1, active + 1);
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = active === null ? 0 : Math.max(0, active - 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = drawn.steps - 1;
    else if (event.key === "Escape") {
      clear();
      return;
    } else return;
    event.preventDefault();
    setKeyboard(true);
    setActive(next);
    setTip(next === null ? null : drawn.tipAt(next));
  };

  const download = () => saveBlob(new Blob([toCsv(table.header, table.rows)], { type: "text/csv" }), `${spec.title || "chart"}.csv`);
  const describe = t(drawn.steps ? S.chart.describeKeys : S.chart.describe, { title: spec.title, type: S.chart.types[spec.type] });

  return (
    <figure className="chart" style={{ margin: 0 }}>
      <div className="chart-head">
        {showTitle ? <h3>{spec.title}</h3> : <span />}
        <div className="row">
          <Segmented
            label={spec.title}
            value={view}
            onChange={setView}
            options={[
              { id: "chart", label: S.common.showChart },
              { id: "table", label: S.common.showTable },
            ]}
          />
          <button type="button" className="btn small" onClick={download}>
            {S.common.downloadCsv}
          </button>
        </div>
      </div>
      {view === "chart" ? (
        <>
          {drawn.legend.length ? (
            <ul className="legend" aria-label={S.chart.legend}>
              {drawn.legend.map((l) => (
                <li key={l.label}>
                  <span className={`swatch${spec.type === "scatter" || spec.type === "line" ? " dot" : ""}`} style={{ background: l.colour }} />
                  {l.label}
                </li>
              ))}
            </ul>
          ) : null}
          {drawn.heat ? (
            <div className="heat-legend">
              <span>{fmtNumber(drawn.heat.lo, 2)}</span>
              <span className="ramp" style={{ background: drawn.heat.diverging ? DIVERGING_CSS : SEQUENTIAL_CSS }} />
              <span>{fmtNumber(drawn.heat.hi, 2)}</span>
              <span>{spec.value}</span>
            </div>
          ) : null}
          <div
            ref={ref}
            className="chart-frame"
            role="group"
            aria-label={describe}
            tabIndex={0}
            onMouseMove={onMove}
            onMouseLeave={clear}
            onKeyDown={onKey}
            onBlur={clear}
          >
            <svg viewBox={`0 0 ${Math.max(280, width)} ${drawn.height}`} aria-hidden="true">
              {drawn.svg}
            </svg>
            {tip ? (
              <div
                className="chart-tip"
                role={keyboard ? "status" : undefined}
                style={{
                  left: `${(tip.x / Math.max(280, width)) * 100}%`,
                  top: `${(tip.y / drawn.height) * 100}%`,
                }}
              >
                <strong>{tip.title}</strong>
                {tip.rows.map((r, i) => (
                  <div className="tip-row" key={i}>
                    <span className="row" style={{ gap: "0.35rem" }}>
                      {r.colour ? <span className="swatch" style={{ background: r.colour }} /> : null}
                      {r.label}
                    </span>
                    <span className="num">{r.value}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </>
      ) : (
        <DataTable header={table.header} rows={table.rows} caption={spec.title} />
      )}
      {[...spec.notes, ...(note ? [note] : [])].map((n) => (
        <figcaption key={n} className="xsmall muted">
          {n}
        </figcaption>
      ))}
    </figure>
  );
}

export function DataTable({
  header,
  rows,
  caption,
  kinds,
  max = 500,
}: {
  header: string[];
  rows: unknown[][];
  caption?: string;
  kinds?: string[];
  max?: number;
}) {
  const shown = rows.slice(0, max);
  const isNum = (i: number) => (kinds ? kinds[i] === "number" : shown.length > 0 && shown.every((r) => r[i] === null || typeof r[i] === "number"));
  return (
    <div className="table-wrap">
      <table className="data">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {header.map((h, i) => (
              <th key={i} scope="col" className={isNum(i) ? "n" : undefined}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, k) => (
            <tr key={k}>
              {r.map((v, i) => (
                <td key={i} className={isNum(i) ? "num" : undefined}>
                  {fmtCell(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > max ? <p className="xsmall muted">{t(S.chart.truncated, { shown: max.toLocaleString(), total: rows.length.toLocaleString() })}</p> : null}
    </div>
  );
}
