"use client";

import { useState } from "react";

import { useConfig } from "@/components/Shell";
import { ErrorNote, Modal, PageHead, Spinner, Toggle, useFeedback } from "@/components/ui";
import { api, download } from "@/lib/api";
import { fmtBytes, fmtDate } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S, t } from "@/lib/strings";

interface Backup {
  name: string;
  size: number;
  created: number;
  kind: string;
}

interface Backups {
  backups: Backup[];
  folder: string;
  schedule: { every: string; keep: number; documents: boolean };
  last_maintenance: number | null;
}

interface ExportMeta {
  id: string;
  status: "building" | "ready" | "failed";
  size?: number;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export default function DataPage() {
  usePageTitle(S.system.dataTitle);
  const { reload } = useConfig();
  const { notify } = useFeedback();
  const backups = useApi<Backups>("/api/backups");
  const [building, setBuilding] = useState(false);
  const [documents, setDocuments] = useState(false);
  const [vectors, setVectors] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [verified, setVerified] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [restore, setRestore] = useState<Backup | null>(null);
  const [replace, setReplace] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const everything = async () => {
    setBuilding(true);
    setError(null);
    try {
      let meta = await api<ExportMeta>("/api/exports", { method: "POST" });
      for (let i = 0; i < 600 && meta.status === "building"; i++) {
        await wait(1000);
        meta = await api<ExportMeta>(`/api/exports/${meta.id}`);
      }
      if (meta.status !== "ready") throw new Error(S.system.exportFailed);
      await download(`/api/exports/${meta.id}/download`, "outlawz-export.zip");
      notify(S.system.exportReady);
    } catch (err) {
      setError(err);
    } finally {
      setBuilding(false);
    }
  };

  const backup = async () => {
    setBusy("new");
    setError(null);
    try {
      const out = await api<{ name: string }>("/api/backups", { json: { include_documents: documents, include_vectors: vectors } });
      notify(t(S.system.backupMade, { name: out.name }));
      backups.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const verify = async (b: Backup) => {
    setBusy(b.name);
    try {
      const out = await api<{ ok: boolean; message: string }>(`/api/backups/${encodeURIComponent(b.name)}/verify`, { method: "POST" });
      setVerified((v) => ({ ...v, [b.name]: out }));
    } catch (err) {
      setVerified((v) => ({ ...v, [b.name]: { ok: false, message: err instanceof Error ? err.message : S.errors.generic } }));
    } finally {
      setBusy(null);
    }
  };

  const doRestore = async () => {
    if (!restore) return;
    const target = restore;
    setRestore(null);
    setBusy(target.name);
    setError(null);
    try {
      const out = await api<{ message: string }>(`/api/backups/${encodeURIComponent(target.name)}/restore`, { json: { replace } });
      notify(out.message);
      await reload();
      backups.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const b = backups.data;
  return (
    <div className="stack">
      <PageHead title={S.system.dataTitle} lede={S.system.dataLede} />
      <ErrorNote error={error ?? backups.error} />
      <section className="card stack tight">
        <h3>{S.system.everything}</h3>
        <p className="small muted" style={{ margin: 0 }}>
          {S.system.everythingHint}
        </p>
        <div>
          <button type="button" className="btn primary" onClick={everything} disabled={building}>
            {building ? S.system.building : S.system.build}
          </button>
        </div>
      </section>
      <section className="card stack">
        <h3>{S.system.backups}</h3>
        <p className="small muted" style={{ margin: 0 }}>
          {S.system.backupHint}
        </p>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <Toggle checked={documents} onChange={setDocuments} label={S.system.withDocuments} />
          <Toggle checked={vectors} onChange={setVectors} label={S.system.withVectors} />
          <button type="button" className="btn primary" onClick={backup} disabled={busy !== null}>
            {busy === "new" ? S.system.building : S.system.newBackup}
          </button>
        </div>
        {!b ? (
          backups.loading ? <Spinner /> : null
        ) : (
          <>
            <p className="xsmall muted" style={{ margin: 0 }}>
              {t(S.system.schedule, { every: b.schedule.every === "off" ? S.common.off : S.settings.options.daily, keep: b.schedule.keep })} · {S.system.folder}:{" "}
              <span className="mono">{b.folder}</span>
              {b.last_maintenance ? ` · ${S.system.lastMaintenance}: ${fmtDate(b.last_maintenance)}` : ""}
            </p>
            {b.backups.length ? (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th scope="col">{S.system.created}</th>
                      <th scope="col">{S.system.backupKind}</th>
                      <th scope="col" className="n">
                        {S.system.size}
                      </th>
                      <th scope="col">
                        <span className="sr-only">{S.common.more}</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {b.backups.map((x) => (
                      <tr key={x.name}>
                        <td>
                          <div>{fmtDate(x.created)}</div>
                          <div className="xsmall muted mono">{x.name}</div>
                          {verified[x.name] ? (
                            <div className={`xsmall ${verified[x.name]!.ok ? "change better" : "change worse"}`} role="status">
                              {verified[x.name]!.message}
                            </div>
                          ) : null}
                        </td>
                        <td>{S.system.backupKinds[x.kind as keyof typeof S.system.backupKinds] ?? x.kind}</td>
                        <td className="num">{fmtBytes(x.size)}</td>
                        <td>
                          <span className="row" style={{ justifyContent: "flex-end" }}>
                            <button type="button" className="btn small" onClick={() => verify(x)} disabled={busy !== null}>
                              {S.system.verify}
                            </button>
                            <button
                              type="button"
                              className="btn small danger"
                              onClick={() => {
                                setReplace(false);
                                setRestore(x);
                              }}
                              disabled={busy !== null}
                            >
                              {busy === x.name ? S.system.restoring : S.system.restore}
                            </button>
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted small">{S.system.noBackups}</p>
            )}
          </>
        )}
      </section>
      {restore ? (
        <Modal
          title={S.system.restoreTitle}
          onClose={() => setRestore(null)}
          actions={
            <>
              <button type="button" className="btn" onClick={() => setRestore(null)}>
                {S.common.cancel}
              </button>
              <button type="button" className="btn danger" onClick={doRestore}>
                {S.system.restore}
              </button>
            </>
          }
        >
          <p>{t(S.system.restoreConfirm, { name: restore.name })}</p>
          <Toggle checked={replace} onChange={setReplace} label={S.system.replace} />
        </Modal>
      ) : null}
    </div>
  );
}
