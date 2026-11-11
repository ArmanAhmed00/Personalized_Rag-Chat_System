"use client";

// Answer text with [n] citations as buttons. A light Markdown subset (paragraphs, bullet and
// numbered lists, **bold**, `code`) is turned into React elements; nothing is ever inserted as
// raw HTML.

import { Fragment, type ReactNode } from "react";

import { S, t } from "@/lib/strings";

interface Props {
  text: string;
  /** Citation numbers that point at a real source. Others are shown as plain text. */
  valid?: Set<number>;
  active?: number | null;
  onCite?: (n: number, target: HTMLElement) => void;
  onHover?: (n: number | null) => void;
  streaming?: boolean;
}

const CITE = /\[(\d{1,3})\]/g;

function inline(text: string, props: Props, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  // Bold and code first, then citations inside the plain pieces.
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  parts.forEach((part, i) => {
    if (!part) return;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      out.push(<strong key={`${key}b${i}`}>{citations(part.slice(2, -2), props, `${key}b${i}`)}</strong>);
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      out.push(<code key={`${key}c${i}`}>{part.slice(1, -1)}</code>);
    } else {
      out.push(<Fragment key={`${key}t${i}`}>{citations(part, props, `${key}t${i}`)}</Fragment>);
    }
  });
  return out;
}

function citations(text: string, props: Props, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(CITE)) {
    const n = Number(match[1]);
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    if (props.onCite && (!props.valid || props.valid.has(n))) {
      out.push(
        <button
          key={`${key}-${at}`}
          type="button"
          className={`cite${props.active === n ? " active" : ""}`}
          aria-label={t(S.cite.label, { n })}
          aria-expanded={props.active === n}
          onClick={(e) => props.onCite?.(n, e.currentTarget)}
          onMouseEnter={() => props.onHover?.(n)}
          onMouseLeave={() => props.onHover?.(null)}
          onFocus={() => props.onHover?.(n)}
          onBlur={() => props.onHover?.(null)}
        >
          {n}
        </button>,
      );
    } else {
      out.push(match[0]);
    }
    last = at + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function CitedText(props: Props) {
  const blocks = props.text.replace(/\r\n/g, "\n").split(/\n{2,}/);
  return (
    <div className={`prose${props.streaming ? " typing" : ""}`}>
      {blocks.map((block, b) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (lines.length && lines.every((l) => /^\s*[-*•]\s+/.test(l))) {
          return (
            <ul key={b}>
              {lines.map((l, i) => (
                <li key={i}>{inline(l.replace(/^\s*[-*•]\s+/, ""), props, `${b}-${i}`)}</li>
              ))}
            </ul>
          );
        }
        if (lines.length && lines.every((l) => /^\s*\d+[.)]\s+/.test(l))) {
          return (
            <ol key={b}>
              {lines.map((l, i) => (
                <li key={i}>{inline(l.replace(/^\s*\d+[.)]\s+/, ""), props, `${b}-${i}`)}</li>
              ))}
            </ol>
          );
        }
        if (lines.length === 1 && /^#{1,4}\s+/.test(lines[0] ?? "")) {
          return <h4 key={b}>{inline((lines[0] ?? "").replace(/^#{1,4}\s+/, ""), props, `${b}`)}</h4>;
        }
        return (
          <p key={b}>
            {lines.map((l, i) => (
              <Fragment key={i}>
                {i > 0 ? <br /> : null}
                {inline(l, props, `${b}-${i}`)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
