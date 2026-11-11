"use client";

import { useState } from "react";

import { Chart } from "@/components/Chart";
import { WindowPicker, type Window } from "@/components/Ops";
import { ErrorNote, PageHead, Spinner, Tile } from "@/components/ui";
import { fmtMs, fmtNumber } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S } from "@/lib/strings";
import type { ChartSpec, Percentiles } from "@/lib/types";

interface Metrics {
  window: string;
  window_s: number;
  requests: number;
  errors: number;
  latency: Percentiles;
  routes: ({ method: string; route: string; requests: number; errors: number; refused: number } & Percentiles)[];
  steps: Record<string, { count: number; outcomes?: Record<string, number>; prompt_tokens?: { total: number; mean: number }; completion_tokens?: { total: number; mean: number } } & Percentiles>;
  groups: Record<string, string[]>;
  timeline: { t: number; requests: number; errors: number }[];
}

function stamp(seconds: number, window: string): string {
  const d = new Date(seconds * 1000);
  return window === "7d"
    ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) + ` ${d.getHours()}h`
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function MetricsPage() {
  usePageTitle(S.system.metricsTitle);
  const [window, setWindow] = useState<Window>("24h");
  const data = useApi<Metrics>(`/api/system/metrics?window=${window}`, 15_000);
  const m = data.data;

  const traffic: ChartSpec | null = m
    ? {
        type: "line",
        title: S.system.traffic,
        x: "time",
        y: "count",
        series: "kind",
        value: null,
        x_kind: "ordered",
        x_label: "",
        y_label: S.system.count,
        data: m.timeline.flatMap((b) => [
          { time: stamp(b.t, window), kind: S.system.requestsLabel, count: b.requests },
          { time: stamp(b.t, window), kind: S.system.errorsLabel, count: b.errors },
        ]),
        notes: [],
      }
    : null;

  return (
    <div className="stack">
      <PageHead title={S.system.metricsTitle} lede={S.system.metricsLede}>
        <WindowPicker value={window} onChange={setWindow} />
      </PageHead>
      <ErrorNote error={data.error} onRetry={data.reload} />
      {!m ? (
        data.loading ? <Spinner /> : null
      ) : (
        <>
          <div className="grid tiles">
            <Tile label={S.system.count} value={fmtNumber(m.requests)} />
            <Tile label={S.system.errorsLabel} value={fmtNumber(m.errors)} />
            <Tile label={S.system.p50} value={fmtMs(m.latency.p50)} />
            <Tile label={S.system.p95Short} value={fmtMs(m.latency.p95)} />
          </div>
          {m.requests && traffic ? (
            <section className="card">
              <Chart spec={traffic} />
            </section>
          ) : (
            <p className="note">{S.system.noData}</p>
          )}
          {m.routes.length ? (
            <section className="card stack tight">
              <h3>{S.system.route}</h3>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th scope="col">{S.system.method}</th>
                      <th scope="col">{S.system.route}</th>
                      <th scope="col" className="n">
                        {S.system.count}
                      </th>
                      <th scope="col" className="n">
                        {S.system.errorsLabel}
                      </th>
                      <th scope="col" className="n">
                        {S.system.refused}
                      </th>
                      <th scope="col" className="n">
                        {S.system.p50}
                      </th>
                      <th scope="col" className="n">
                        {S.system.p95Short}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.routes.map((r) => (
                      <tr key={`${r.method} ${r.route}`}>
                        <td className="mono">{r.method}</td>
                        <td className="mono">{r.route}</td>
                        <td className="num">{fmtNumber(r.requests)}</td>
                        <td className="num">{fmtNumber(r.errors)}</td>
                        <td className="num">{fmtNumber(r.refused)}</td>
                        <td className="num">{fmtMs(r.p50)}</td>
                        <td className="num">{fmtMs(r.p95)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
          <div className="grid wide-cells">
            {Object.entries(m.groups).map(([group, names]) => {
              const rows = names.filter((n) => m.steps[n]);
              if (!rows.length) return null;
              return (
                <section key={group} className="card stack tight">
                  <h3>{group}</h3>
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          <th scope="col">{S.system.step}</th>
                          <th scope="col" className="n">
                            {S.system.count}
                          </th>
                          <th scope="col" className="n">
                            {S.system.p50}
                          </th>
                          <th scope="col" className="n">
                            {S.system.p95Short}
                          </th>
                          <th scope="col" className="n">
                            {S.system.tokens}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((n) => {
                          const s = m.steps[n]!;
                          const tokens = (s.prompt_tokens?.total ?? 0) + (s.completion_tokens?.total ?? 0);
                          return (
                            <tr key={n}>
                              <td className="mono">{n}</td>
                              <td className="num">{fmtNumber(s.count)}</td>
                              <td className="num">{fmtMs(s.p50)}</td>
                              <td className="num">{fmtMs(s.p95)}</td>
                              <td className="num">{tokens ? fmtNumber(tokens) : "—"}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </section>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
