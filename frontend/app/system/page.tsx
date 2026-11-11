"use client";

import { ShareBar } from "@/components/Ops";
import { ErrorNote, PageHead, Spinner, StatusBadge, Tile } from "@/components/ui";
import { fmtBytes, fmtMs, fmtNumber } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S } from "@/lib/strings";
import type { Status } from "@/lib/types";

interface Overview {
  version: string;
  uptime_s: number;
  library: string;
  documents: { total: number; by_status: Record<string, number>; by_type: Record<string, number>; passages: number; bytes: number };
  chats: number;
  queue: { waiting: number; reading: number };
  model: { llm: string; llm_model: string; embed_model: string; cloud: boolean };
  last_24h: { requests: number; errors: number; p95_ms: number | null };
  storage: { app_folder_bytes: number; logs_bytes: number; free_bytes?: number; total_bytes?: number };
  activity: { events: number };
}

function uptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

export default function SystemOverview() {
  usePageTitle(S.system.overviewTitle);
  const data = useApi<Overview>("/api/system/overview", 10_000);
  const o = data.data;
  return (
    <div className="stack">
      <PageHead title={S.system.overviewTitle} lede={S.system.overviewLede} />
      <ErrorNote error={data.error} onRetry={data.reload} />
      {!o ? (
        data.loading ? <Spinner /> : null
      ) : (
        <>
          <div className="grid tiles">
            <Tile label={S.system.documents} value={fmtNumber(o.documents.total)} sub={fmtBytes(o.documents.bytes)} />
            <Tile label={S.system.passages} value={fmtNumber(o.documents.passages)} />
            <Tile label={S.system.chats} value={fmtNumber(o.chats)} />
            <Tile label={S.system.queue} value={fmtNumber(o.queue.waiting + o.queue.reading)} />
            <Tile label={S.system.requests} value={fmtNumber(o.last_24h.requests)} />
            <Tile label={S.system.errors} value={fmtNumber(o.last_24h.errors)} />
            <Tile label={S.system.p95} value={fmtMs(o.last_24h.p95_ms)} />
          </div>
          <div className="grid two">
            <section className="card stack">
              <h3>{S.system.byType}</h3>
              {o.documents.total ? (
                <ShareBar label={S.system.byType} items={Object.entries(o.documents.by_type).map(([k, v]) => ({ label: k.toUpperCase(), value: v }))} />
              ) : (
                <p className="muted small">{S.library.empty}</p>
              )}
              <h3>{S.system.byStatus}</h3>
              <div className="row" style={{ flexWrap: "wrap" }}>
                {Object.entries(o.documents.by_status).map(([k, v]) => (
                  <span key={k} className="row" style={{ gap: "0.35rem" }}>
                    <StatusBadge status={k as Status} />
                    <span className="num">{v}</span>
                  </span>
                ))}
              </div>
            </section>
            <section className="card stack tight">
              <h3>{S.system.storage}</h3>
              <dl className="grid two small" style={{ margin: 0 }}>
                <div>
                  <dt className="label">{S.system.appFolder}</dt>
                  <dd className="num">{fmtBytes(o.storage.app_folder_bytes)}</dd>
                </div>
                <div>
                  <dt className="label">{S.system.logs}</dt>
                  <dd className="num">{fmtBytes(o.storage.logs_bytes)}</dd>
                </div>
                <div>
                  <dt className="label">{S.system.free}</dt>
                  <dd className="num">{fmtBytes(o.storage.free_bytes)}</dd>
                </div>
                <div>
                  <dt className="label">{S.system.uptime}</dt>
                  <dd>{uptime(o.uptime_s)}</dd>
                </div>
                <div>
                  <dt className="label">{S.system.model}</dt>
                  <dd className="mono">{o.model.llm_model}</dd>
                </div>
                <div>
                  <dt className="label">{S.system.version}</dt>
                  <dd>{o.version}</dd>
                </div>
              </dl>
              <p className="xsmall muted mono" style={{ overflowWrap: "anywhere" }}>
                {o.library}
              </p>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
