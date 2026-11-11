import type { ReactNode } from "react";

/** Mark the matched words in a passage (case-insensitive, whole-word prefixes). */
export function Highlight({ text, words }: { text: string; words: string[] }): ReactNode {
  const clean = words.filter((w) => w.length > 1).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!clean.length) return text;
  const pattern = new RegExp(`\\b(${clean.join("|")})\\w*`, "gi");
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    out.push(
      <mark className="hl" key={at}>
        {m[0]}
      </mark>,
    );
    last = at + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
