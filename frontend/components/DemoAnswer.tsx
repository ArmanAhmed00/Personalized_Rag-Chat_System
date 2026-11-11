"use client";

// The landing page's example: a question, a cited answer and the two pages it came from. The
// highlight moves between citations by itself (unless motion is reduced) and follows hover/focus.

import { useEffect, useState } from "react";

import { useReducedMotion } from "@/lib/hooks";
import { S } from "@/lib/strings";

import { CitedText } from "./CitedText";

export function DemoAnswer() {
  const reduced = useReducedMotion();
  const [auto, setAuto] = useState(1);
  const [hover, setHover] = useState<number | null>(null);
  const active = hover ?? auto;

  useEffect(() => {
    if (reduced || hover !== null) return;
    const timer = setInterval(() => setAuto((n) => (n === 1 ? 2 : 1)), 2800);
    return () => clearInterval(timer);
  }, [reduced, hover]);

  return (
    <section className="demo" aria-label={S.home.demoLabel}>
      <div className="question">{S.home.demoQuestion}</div>
      <CitedText text={S.home.demoAnswer} active={active} onCite={(n) => setHover(n)} onHover={setHover} />
      {S.home.demoSources.map((source, i) => (
        <div className="page" key={source.where} aria-current={active === i + 1 ? "true" : undefined}>
          <div className="where">
            <span>
              [{i + 1}] {source.where}
            </span>
          </div>
          {source.before}
          <mark className={active === i + 1 ? "on" : undefined}>{source.quote}</mark>
          {source.after}
        </div>
      ))}
      <p className="xsmall muted" style={{ margin: 0 }}>
        {S.home.demoHint}
      </p>
    </section>
  );
}
