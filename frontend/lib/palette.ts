// Chart colours. These match the --series-* tokens and were checked with the palette validator
// (lightness band, chroma floor, colour-blind separation of neighbouring slots, contrast). Slots
// are assigned in this fixed order by first appearance, never cycled: a ninth series becomes
// "Other". Text never uses these colours; a swatch or mark beside it carries the identity.

export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
export const OTHER = "#9aa3b2";
export const SURFACE = "#ffffff";

/** Sequential (magnitude): one hue, light to dark. */
const BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
/** Diverging (sign): red ← neutral grey → blue, equal steps per arm. */
const RED = ["#fbe1df", "#f5b4ae", "#ec7f78", "#e34948", "#b8302f", "#84201f"];
const NEUTRAL = "#f0efec";

export function seriesColour(index: number, name?: unknown): string {
  if (name === "Other") return OTHER;
  return SERIES[index] ?? OTHER;
}

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function mix(a: string, b: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  const c = (x: number, y: number) => Math.round(x + (y - x) * t).toString(16).padStart(2, "0");
  return `#${c(r1, r2)}${c(g1, g2)}${c(b1, b2)}`;
}

function along(stops: string[], t: number): string {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  return mix(stops[i] ?? stops[0]!, stops[i + 1] ?? stops[stops.length - 1]!, x - i);
}

export function sequential(t: number): string {
  return along(BLUE, t);
}

/** ``t`` in [-1, 1]; 0 is the neutral midpoint. */
export function diverging(t: number): string {
  if (t >= 0) return along([NEUTRAL, ...BLUE.slice(1)], t);
  return along([NEUTRAL, ...RED.slice(1)], -t);
}

/** White or ink text, whichever reads better on a fill. */
export function inkOn(fill: string): string {
  const [r, g, b] = hexToRgb(fill).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.35 ? "#14213d" : "#ffffff";
}

export const DIVERGING_CSS = `linear-gradient(90deg, ${RED.slice(1).reverse().join(", ")}, ${NEUTRAL}, ${BLUE.slice(1).join(", ")})`;
export const SEQUENTIAL_CSS = `linear-gradient(90deg, ${BLUE.join(", ")})`;
