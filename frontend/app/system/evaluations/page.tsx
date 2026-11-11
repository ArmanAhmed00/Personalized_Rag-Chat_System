"use client";

import { useEffect, useState } from "react";

import { ErrorNote, PageHead, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, fmtMs, fmtNumber } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S, t } from "@/lib/strings";
import type { EvalRun } from "@/lib/types";

/** Flatten a suite's results to "metric → number" rows (nested objects become dotted names). */
function flatten(value: unknown, prefix = ""): [string, number][] {
  if (typeof value === "number") return [[prefix, value]];
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>).flatMap(([k, v]) => flatten(v, prefix ? `${prefix}.${k}` : k));
}

export default function Evaluations() {
  usePageTitle(S.system.evalTitle);
  const [running, setRunning] = useState(false);
  const runs = useApi<{ runs: EvalRun[]; suites: string[] }>("/api/evals", running ? 2000 : false);
  const [chosen, setChosen] = useState<string | null>(null);
  const run = useApi<EvalRun>(chosen ? `/api/evals/${chosen}` : null, running && chosen ? 2000 : false);
  const [error, setError] = useState<unknown>(null);

  const active = runs.data?.runs.some((r) => r.status === "running") ?? false;
  useEffect(() => setRunning(active), [active]);
  useEffect(() => {
    const first = runs.data?.runs[0];
    if (!chosen && first) setChosen(first.id);
  }, [runs.data, chosen]);

  const start = async () => {
    setError(null);
    try {
      const out = await api<{ id: string }>("/api/evals", { json: {} });
      setChosen(out.id);
      setRunning(true);
      runs.reload();
    } catch (err) {
      setError(err);
    }
  };

  useEffect(() => {
    if (!running && chosen) run.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  const r = run.data;
  return (
    <div className="stack">
      <PageHead title={S.system.evalTitle} lede={S.system.evalLede}>
        <button type="button" className="btn primary" onClick={start} disabled={active}>
          {S.system.runEval}
        </button>
      </PageHead>
      <ErrorNote error={error ?? runs.error} />
      <div className="grid two" style={{ alignItems: "start", gridTemplateColumns: "minmax(0, 18rem) minmax(0, 1fr)" }}>
        <section className="card stack tight">
          <h3>{S.system.runs}</h3>
          {runs.data?.runs.length ? (
            <ul className="doc-list">
              {runs.data.runs.map((x) => (
                <li key={x.id}>
                  <button
                    type="button"
                    className="btn ghost small"
                    style={{ width: "100%", justifyContent: "space-between", fontWeight: chosen === x.id ? 700 : 500 }}
                    aria-current={chosen === x.id}
                    onClick={() => setChosen(x.id)}
                  >
                    <span>{fmtDate(x.started)}</span>
                    <span className="row" style={{ gap: "0.3rem" }}>
                      {x.status === "running" ? <span className="badge teal">{x.progress ?? "…"}</span> : null}
                      {x.status === "failed" ? <span className="badge danger">!</span> : null}
                      {x.better ? <span className="change better">▲{x.better}</span> : null}
                      {x.worse ? <span className="change worse">▼{x.worse}</span> : null}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : runs.loading ? (
            <Spinner />
          ) : (
            <p className="muted small">{S.system.noRuns}</p>
          )}
        </section>
        <section className="card stack">
          {!r ? (
            run.loading ? <Spinner /> : <p className="muted small">{S.system.noRuns}</p>
          ) : r.status === "running" ? (
            <Spinner label={t(S.system.running, { progress: r.progress ?? "" })} />
          ) : r.status === "failed" ? (
            <p className="note danger">{r.error}</p>
          ) : (
            <>
              <p className="small muted" style={{ margin: 0 }}>
                {S.system.started} {fmtDate(r.started)} · {S.system.duration} {fmtMs((r.seconds ?? 0) * 1000)}
              </p>
              <h3>{S.system.changes}</h3>
              {r.changes?.length ? (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th scope="col">{S.system.metric}</th>
                        <th scope="col" className="n">
                          {S.system.before}
                        </th>
                        <th scope="col" className="n">
                          {S.system.after}
                        </th>
                        <th scope="col" className="n">
                          {S.analysis.change}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.changes.map((c) => (
                        <tr key={c.metric}>
                          <td className="mono">{c.metric}</td>
                          <td className="num">{fmtNumber(c.before, 3)}</td>
                          <td className="num">{fmtNumber(c.after, 3)}</td>
                          <td className={`num change ${c.better ? "better" : "worse"}`}>
                            {c.arrow} {fmtNumber(c.change, 3)} <span className="sr-only">{c.better ? S.system.better : S.system.worse}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="muted small">{S.system.noChanges}</p>
              )}
              <h3>{S.system.results}</h3>
              <div className="grid two">
                {Object.entries(r.results ?? {}).map(([suite, values]) => (
                  <div key={suite} className="card flat stack tight">
                    <strong>{suite}</strong>
                    <table className="data">
                      <tbody>
                        {flatten(values).map(([k, v]) => (
                          <tr key={k}>
                            <td className="mono xsmall">{k}</td>
                            <td className="num">{fmtNumber(v, 3)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
              {r.settings ? (
                <details>
                  <summary className="small">{S.system.settingsUsed}</summary>
                  <pre className="mono xsmall" style={{ whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(r.settings, null, 2)}
                  </pre>
                </details>
              ) : null}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
