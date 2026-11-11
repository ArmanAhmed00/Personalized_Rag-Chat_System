// Formatting for people: numbers, sizes, durations and times.

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
const compactFormat = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

export function fmtNumber(value: unknown, digits?: number): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value !== "number") return String(value);
  if (!Number.isFinite(value)) return "—";
  if (digits !== undefined) {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value);
  }
  const abs = Math.abs(value);
  if (abs >= 1000) return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
  return numberFormat.format(value);
}

export function fmtCompact(value: number): string {
  return Math.abs(value) >= 10_000 ? compactFormat.format(value) : fmtNumber(value, 2);
}

export function fmtPercent(share: number | null | undefined, digits = 0): string {
  if (share === null || share === undefined || !Number.isFinite(share)) return "—";
  return `${(share * 100).toFixed(digits)}%`;
}

export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  return `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`;
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function fmtAgo(seconds: number): string {
  const diff = seconds - Date.now() / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return relative.format(Math.round(diff), "second");
  if (abs < 2700) return relative.format(Math.round(diff / 60), "minute");
  if (abs < 64800) return relative.format(Math.round(diff / 3600), "hour");
  if (abs < 7 * 86400) return relative.format(Math.round(diff / 86400), "day");
  return fmtDate(seconds);
}

export function fmtDate(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtTime(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtCell(value: unknown): string {
  if (typeof value === "number") return fmtNumber(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value === null || value === undefined) return "—";
  return String(value);
}

export function sourceLabel(p: {
  document: string;
  page?: number | null;
  page_end?: number | null;
  rows?: number[] | null;
  chapter?: string | null;
  section?: string | null;
  kind?: string;
}): string {
  const parts = [p.document];
  if (p.page && p.page_end && p.page_end !== p.page) parts.push(`pages ${p.page}–${p.page_end}`);
  else if (p.page) parts.push(`page ${p.page}`);
  if (p.rows && p.rows.length) {
    const [first, last] = [p.rows[0], p.rows[p.rows.length - 1]];
    parts.push(first === last ? `row ${first}` : `rows ${first}–${last}`);
  }
  const where = [p.chapter, p.section].filter(Boolean).join(" › ");
  if (where && !["rows", "schema", "profile"].includes(p.kind ?? "")) parts.push(where);
  return parts.join(", ");
}

/** CSV text from a header and rows (RFC 4180 quoting). */
export function toCsv(header: string[], rows: unknown[][]): string {
  const cell = (v: unknown) => {
    const text = v === null || v === undefined ? "" : String(v);
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
}
