"use client";

import { useState } from "react";

import { S, t } from "@/lib/strings";
import type { AnalysisResult } from "@/lib/types";

import { Chart, DataTable } from "./Chart";

/** One computed analysis: summary, chart (or why there's none), the full table and the plan. */
export function ResultView({ result, summary, fixes }: { result: AnalysisResult; summary?: string; fixes?: string[] }) {
  const [planOpen, setPlanOpen] = useState(false);
  return (
    <div className="stack">
      <div className="stack tight">
        <h3 style={{ margin: 0 }}>{result.title}</h3>
        <p style={{ margin: 0 }}>{summary ?? result.summary}</p>
        <p className="xsmall muted" style={{ margin: 0 }}>
          {t(S.analysis.rowsUsed, { used: result.rows_used.toLocaleString(), total: result.rows_total.toLocaleString() })}
        </p>
      </div>
      {fixes?.length ? (
        <div className="note">
          {fixes.map((f) => (
            <p key={f}>{f}</p>
          ))}
        </div>
      ) : null}
      {result.chart ? (
        <Chart spec={result.chart} showTitle={result.chart.title !== result.title} />
      ) : result.columns.length ? (
        <>
          {result.chart_note ? <p className="xsmall muted">{result.chart_note}</p> : null}
          <DataTable header={result.columns.map((c) => c.name)} kinds={result.columns.map((c) => c.kind)} rows={result.rows} caption={result.title} />
        </>
      ) : null}
      {result.chart && result.columns.length ? (
        <details>
          <summary className="small">{S.common.showTable}</summary>
          <DataTable header={result.columns.map((c) => c.name)} kinds={result.columns.map((c) => c.kind)} rows={result.rows} caption={result.title} />
        </details>
      ) : null}
      {result.notes.map((n) => (
        <p key={n} className="xsmall muted" style={{ margin: 0 }}>
          {n}
        </p>
      ))}
      <div>
        <button type="button" className="btn small ghost" aria-expanded={planOpen} onClick={() => setPlanOpen((v) => !v)}>
          {S.chat.showPlan}
        </button>
        {planOpen ? (
          <pre className="mono xsmall card flat" style={{ whiteSpace: "pre-wrap", margin: "0.5rem 0 0" }}>
            {JSON.stringify(result.plan, null, 2)}
          </pre>
        ) : null}
      </div>
    </div>
  );
}
