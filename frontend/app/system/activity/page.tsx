"use client";

import { useState } from "react";

import { ErrorNote, Field, PageHead, Spinner, useFeedback } from "@/components/ui";
import { api, download } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S } from "@/lib/strings";
import type { ActivityEvent } from "@/lib/types";

function seconds(local: string): number | null {
  if (!local) return null;
  const ms = new Date(local).getTime();
  return Number.isFinite(ms) ? ms / 1000 : null;
}

export default function Activity() {
  usePageTitle(S.system.activityTitle);
  const { notify } = useFeedback();
  const [action, setAction] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [check, setCheck] = useState<{ ok: boolean; message: string } | null>(null);
  const query = new URLSearchParams();
  if (action) query.set("action", action);
  const since = seconds(from);
  const until = seconds(to);
  if (since !== null) query.set("since", String(since));
  if (until !== null) query.set("until", String(until));
  const qs = query.toString();
  const data = useApi<{ events: ActivityEvent[]; actions: string[] }>(`/api/system/activity?limit=500${qs ? `&${qs}` : ""}`);

  const verify = async () => {
    try {
      setCheck(await api<{ ok: boolean; message: string }>("/api/system/activity/check"));
    } catch (err) {
      setCheck({ ok: false, message: err instanceof Error ? err.message : S.errors.generic });
    }
  };

  const exportCsv = async () => {
    try {
      await download(`/api/system/activity.csv${qs ? `?${qs}` : ""}`, "outlawz-activity.csv");
    } catch (err) {
      notify(err instanceof Error ? err.message : S.errors.generic);
    }
  };

  return (
    <div className="stack">
      <PageHead title={S.system.activityTitle} lede={S.system.activityLede}>
        <button type="button" className="btn" onClick={verify}>
          {S.system.check}
        </button>
        <button type="button" className="btn" onClick={exportCsv}>
          {S.system.exportCsv}
        </button>
      </PageHead>
      {check ? (
        <p className={`note ${check.ok ? "good" : "danger"}`} role="status">
          {check.message}
        </p>
      ) : null}
      <section className="card grid three">
        <Field label={S.system.action}>
          <select className="select" value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">{S.system.allActions}</option>
            {(data.data?.actions ?? []).map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </Field>
        <Field label={S.system.from}>
          <input className="input" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label={S.system.to}>
          <input className="input" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
        </Field>
      </section>
      <ErrorNote error={data.error} onRetry={data.reload} />
      {!data.data ? (
        data.loading ? <Spinner /> : null
      ) : data.data.events.length ? (
        <div className="table-wrap card tight">
          <table className="data">
            <thead>
              <tr>
                <th scope="col" className="n">
                  {S.system.seq}
                </th>
                <th scope="col">{S.system.time}</th>
                <th scope="col">{S.system.action}</th>
                <th scope="col">{S.system.details}</th>
              </tr>
            </thead>
            <tbody>
              {data.data.events.map((e) => (
                <tr key={e.seq}>
                  <td className="num">{e.seq}</td>
                  <td>{fmtDate(e.t)}</td>
                  <td className="mono">{e.action}</td>
                  <td className="mono xsmall" style={{ overflowWrap: "anywhere" }}>
                    {Object.entries(e.ids)
                      .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`)
                      .join(" · ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="note">{S.system.noData}</p>
      )}
    </div>
  );
}
