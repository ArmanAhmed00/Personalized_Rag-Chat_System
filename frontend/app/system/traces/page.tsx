"use client";

import { useState } from "react";

import { ShareBar, Waterfall, WindowPicker, type Window } from "@/components/Ops";
import { ErrorNote, PageHead, Spinner } from "@/components/ui";
import { fmtMs, fmtTime } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S, t } from "@/lib/strings";
import type { Trace } from "@/lib/types";

type Kind = "answer" | "search" | "ingest" | "analysis";

interface Traces {
  traces: Trace[];
  where_time_goes: Record<string, { traces: number; mean_ms: number; steps: { name: string; ms: number; share: number; mean_ms: number }[] }>;
  sample: number;
  slow_ms: number;
}

export default function TracesPage() {
  usePageTitle(S.system.tracesTitle);
  const [window, setWindow] = useState<Window>("24h");
  const [kind, setKind] = useState<Kind | "">("");
  const data = useApi<Traces>(`/api/system/traces?window=${window}${kind ? `&kind=${kind}` : ""}&limit=200`, 20_000);
  const [chosen, setChosen] = useState<Trace | null>(null);
  const d = data.data;
  return (
    <div className="stack">
      <PageHead title={S.system.tracesTitle} lede={S.system.tracesLede}>
        <WindowPicker value={window} onChange={setWindow} />
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value as Kind | "")} aria-label={S.system.kind} style={{ width: "auto" }}>
          <option value="">{S.system.allKinds}</option>
          {(Object.keys(S.system.kinds) as Kind[]).map((k) => (
            <option key={k} value={k}>
              {S.system.kinds[k]}
            </option>
          ))}
        </select>
      </PageHead>
      <ErrorNote error={data.error} onRetry={data.reload} />
      {!d ? (
        data.loading ? <Spinner /> : null
      ) : (
        <>
          <section className="card stack">
            <h3>{S.system.whereTime}</h3>
            {Object.keys(d.where_time_goes).length ? (
              <div className="grid two">
                {Object.entries(d.where_time_goes).map(([k, w]) => (
                  <div key={k} className="stack tight">
                    <div className="row between small">
                      <strong>{S.system.kinds[k as Kind] ?? k}</strong>
                      <span className="muted">
                        {w.traces} · {S.system.mean} {fmtMs(w.mean_ms)}
                      </span>
                    </div>
                    <ShareBar label={S.system.kinds[k as Kind] ?? k} items={w.steps.map((s) => ({ label: s.name, value: s.mean_ms }))} format={fmtMs} />
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted small">{S.system.noTraces}</p>
            )}
          </section>
          <div className="grid two" style={{ alignItems: "start" }}>
            <section className="card stack tight">
              {d.traces.length ? (
                <div className="table-wrap" style={{ maxHeight: "32rem", overflowY: "auto" }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th scope="col">{S.system.time}</th>
                        <th scope="col">{S.system.kind}</th>
                        <th scope="col" className="n">
                          {S.system.total}
                        </th>
                        <th scope="col">{S.system.status}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.traces.map((tr) => (
                        <tr
                          key={tr.id}
                          className={`clickable${chosen?.id === tr.id ? " selected" : ""}`}
                          tabIndex={0}
                          onClick={() => setChosen(tr)}
                          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setChosen(tr)}
                          aria-selected={chosen?.id === tr.id}
                        >
                          <td>{fmtTime(tr.t)}</td>
                          <td>{S.system.kinds[tr.kind as Kind] ?? tr.kind}</td>
                          <td className="num">{fmtMs(tr.total_ms)}</td>
                          <td>
                            <span className={`badge ${tr.status === "ok" ? "" : "danger"}`}>{tr.code ?? tr.status}</span>
                            {tr.slow ? <span className="badge warn" style={{ marginLeft: 4 }}>{S.system.slow}</span> : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="muted small">{S.system.noTraces}</p>
              )}
              <p className="xsmall muted">{t(S.system.sampling, { share: `${Math.round(d.sample * 100)}%`, ms: fmtMs(d.slow_ms) })}</p>
            </section>
            <section className="card stack tight">
              {chosen ? (
                <>
                  <div className="row between small">
                    <strong>{S.system.kinds[chosen.kind as Kind] ?? chosen.kind}</strong>
                    <span className="num">{fmtMs(chosen.total_ms)}</span>
                  </div>
                  <Waterfall spans={chosen.spans} total={chosen.total_ms} />
                  {chosen.counts ? (
                    <p className="xsmall muted mono">
                      {Object.entries(chosen.counts)
                        .map(([k, v]) => `${k}=${v}`)
                        .join(" · ")}
                    </p>
                  ) : null}
                  <p className="xsmall muted mono">{chosen.id}</p>
                </>
              ) : (
                <p className="muted small">{S.system.chooseTrace}</p>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
