"use client";

import { useState } from "react";

import { Waterfall } from "@/components/Ops";
import { ErrorNote, PageHead, Spinner, StatusBadge, Tile, useFeedback } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, fmtMs, fmtNumber } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S, t } from "@/lib/strings";
import type { Doc, Percentiles, Status, Trace } from "@/lib/types";

interface Processing {
  waiting: number;
  queue: { id: string; name: string; status: Status; message: string }[];
  current: { id: string; name: string; seconds: number; status: Status; progress: { step: string; done: number; total: number; message: string } }[];
  failures: { code: string; count: number; message: string; documents: { id: string; name: string }[] }[];
  time_per_document: { count: number } & Percentiles;
  steps: Record<string, Percentiles>;
  workers: number;
}

interface Timeline {
  id: string;
  name: string;
  status: Status;
  added_at: number;
  ready_at: number | null;
  error: { code: string; message: string } | null;
  runs: Trace[];
}

export default function ProcessingPage() {
  usePageTitle(S.system.processingTitle);
  const { notify } = useFeedback();
  const data = useApi<Processing>("/api/system/processing", 2500);
  const docs = useApi<{ documents: Doc[] }>("/api/documents");
  const [doc, setDoc] = useState("");
  const timeline = useApi<Timeline>(doc ? `/api/system/processing/${doc}` : null);
  const p = data.data;

  const act = async (path: string) => {
    try {
      await api(path, { method: "POST" });
      data.reload();
    } catch (err) {
      notify(err instanceof Error ? err.message : S.errors.generic);
    }
  };

  return (
    <div className="stack">
      <PageHead title={S.system.processingTitle} lede={S.system.processingLede} />
      <ErrorNote error={data.error} onRetry={data.reload} />
      {!p ? (
        data.loading ? <Spinner /> : null
      ) : (
        <>
          <div className="grid tiles">
            <Tile label={S.system.current} value={fmtNumber(p.current.length)} sub={t(S.system.workers, { n: p.workers })} />
            <Tile label={S.system.waiting} value={fmtNumber(p.waiting)} />
            <Tile label={`${S.system.perDocument} · ${S.system.p50}`} value={fmtMs(p.time_per_document.p50)} sub={t(S.system.timed, { n: p.time_per_document.count })} />
            <Tile label={`${S.system.perDocument} · ${S.system.p95Short}`} value={fmtMs(p.time_per_document.p95)} />
          </div>
          <div className="grid two" style={{ alignItems: "start" }}>
            <section className="card stack tight">
              <h3>{S.system.current}</h3>
              {p.current.length ? (
                <ul className="doc-list">
                  {p.current.map((c) => {
                    const share = c.progress.total ? c.progress.done / c.progress.total : null;
                    return (
                      <li key={c.id} className="stack tight">
                        <div className="row between">
                          <strong>{c.name}</strong>
                          <span className="row" style={{ gap: "0.35rem" }}>
                            <StatusBadge status={c.status} />
                            <span className="xsmall muted">{t(S.system.seconds, { n: c.seconds })}</span>
                            <button type="button" className="btn small ghost" onClick={() => act(`/api/documents/${c.id}/cancel`)}>
                              {S.system.cancel}
                            </button>
                          </span>
                        </div>
                        <div className="progress" role="progressbar" aria-label={c.progress.message} aria-valuemin={0} aria-valuemax={100} aria-valuenow={share === null ? undefined : Math.round(share * 100)}>
                          <span style={{ width: share === null ? "15%" : `${Math.max(3, share * 100)}%` }} />
                        </div>
                        <span className="xsmall muted">{c.progress.message}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="muted small">{S.system.noCurrent}</p>
              )}
              <h3>{S.system.waiting}</h3>
              {p.queue.length ? (
                <ol className="doc-list small">
                  {p.queue.map((q) => (
                    <li key={q.id} className="row between">
                      <span>{q.name}</span>
                      <span className="xsmall muted">{q.message}</span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="muted small">{S.system.noWaiting}</p>
              )}
            </section>
            <section className="card stack tight">
              <h3>{S.system.failures}</h3>
              {p.failures.length ? (
                <ul className="doc-list">
                  {p.failures.map((f) => (
                    <li key={f.code} className="stack tight">
                      <div className="row between">
                        <span className="badge danger">{f.code}</span>
                        <span className="num">{f.count}</span>
                      </div>
                      <span className="small">{f.message}</span>
                      <ul className="stack tight" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                        {f.documents.map((d) => (
                          <li key={d.id} className="row between xsmall">
                            <span>{d.name}</span>
                            <button type="button" className="btn small ghost" onClick={() => act(`/api/documents/${d.id}/reread`)}>
                              {S.system.retry}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">{S.system.noFailures}</p>
              )}
              <h3>{S.system.step}</h3>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th scope="col">{S.system.step}</th>
                      <th scope="col" className="n">
                        {S.system.p50}
                      </th>
                      <th scope="col" className="n">
                        {S.system.p95Short}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(p.steps).map(([k, v]) => (
                      <tr key={k}>
                        <td className="mono">{k}</td>
                        <td className="num">{fmtMs(v.p50)}</td>
                        <td className="num">{fmtMs(v.p95)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
          <section className="card stack">
            <div className="row between">
              <h3>{S.system.timeline}</h3>
              <select className="select" value={doc} onChange={(e) => setDoc(e.target.value)} aria-label={S.system.pickDoc} style={{ width: "auto", maxWidth: "22rem" }}>
                <option value="">{S.system.pickDoc}</option>
                {(docs.data?.documents ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.path}
                  </option>
                ))}
              </select>
            </div>
            <ErrorNote error={timeline.error} />
            {timeline.data ? (
              <div className="stack tight">
                <div className="row small">
                  <StatusBadge status={timeline.data.status} />
                  <span className="muted">
                    {fmtDate(timeline.data.added_at)}
                    {timeline.data.ready_at ? ` → ${fmtDate(timeline.data.ready_at)}` : ""}
                  </span>
                </div>
                {timeline.data.error ? <p className="note danger">{timeline.data.error.message}</p> : null}
                {timeline.data.runs.length ? (
                  timeline.data.runs.map((run) => (
                    <div key={run.id} className="card flat stack tight">
                      <div className="row between xsmall">
                        <span>{fmtDate(run.t)}</span>
                        <span className="num">{fmtMs(run.total_ms)}</span>
                      </div>
                      <Waterfall spans={run.spans} total={run.total_ms} />
                    </div>
                  ))
                ) : (
                  <p className="muted small">{S.system.noRuns2}</p>
                )}
              </div>
            ) : null}
          </section>
        </>
      )}
    </div>
  );
}
