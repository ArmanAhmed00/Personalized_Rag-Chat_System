"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/Icon";
import { Upload } from "@/components/Upload";
import { CopyButton, Empty, ErrorNote, PageHead, Spinner, StatusBadge, plural, useFeedback } from "@/components/ui";
import { api, openDocument } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S, t } from "@/lib/strings";
import type { Doc } from "@/lib/types";

interface Folder {
  name: string;
  path: string;
  folders: Folder[];
  documents: Doc[];
}

const SETTLED = new Set(["ready", "failed"]);

function count(folder: Folder): number {
  return folder.documents.length + folder.folders.reduce((n, f) => n + count(f), 0);
}

function allFolders(folder: Folder): string[] {
  return folder.folders.flatMap((f) => [f.path, ...allFolders(f)]);
}

function Tree({ folder, current, onPick }: { folder: Folder; current: string | null; onPick: (path: string | null) => void }) {
  return (
    <ul>
      {folder.folders.map((f) => (
        <li key={f.path}>
          <button type="button" aria-current={current === f.path} onClick={() => onPick(f.path)}>
            <Icon name="folder" size={15} />
            <span className="grow">{f.name}</span>
            <span className="xsmall muted">{count(f)}</span>
          </button>
          {f.folders.length ? <Tree folder={f} current={current} onPick={onPick} /> : null}
        </li>
      ))}
    </ul>
  );
}

export default function Library() {
  usePageTitle(S.library.title);
  const { confirm, notify } = useFeedback();
  const [poll, setPoll] = useState(4000);
  const tree = useApi<{ root: string; tree: Folder }>("/api/library/tree", poll);
  const [folder, setFolder] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<unknown>(null);

  const docs = useMemo(() => {
    const out: Doc[] = [];
    const walk = (f: Folder) => {
      out.push(...f.documents);
      f.folders.forEach(walk);
    };
    if (tree.data) walk(tree.data.tree);
    return out;
  }, [tree.data]);

  const busy = docs.some((d) => !SETTLED.has(d.status));
  useEffect(() => setPoll(busy ? 1500 : 8000), [busy]);

  const shown = docs.filter(
    (d) =>
      (folder === null || d.folder === folder || d.folder.startsWith(`${folder}/`)) &&
      (!filter.trim() || d.path.toLowerCase().includes(filter.trim().toLowerCase())),
  );

  const act = async (work: () => Promise<unknown>, message?: string) => {
    setError(null);
    try {
      await work();
      if (message) notify(message);
    } catch (err) {
      setError(err);
    }
    tree.reload();
  };

  const remove = async (doc: Doc) => {
    if (!(await confirm(t(S.library.deleteConfirm, { name: doc.name }), { danger: true, yes: S.common.delete }))) return;
    await act(() => api(`/api/documents/${doc.id}`, { method: "DELETE" }), S.library.deleted);
  };

  const samples = () =>
    act(async () => {
      const out = await api<{ message: string }>("/api/library/samples", { method: "POST" });
      notify(out.message);
    });

  return (
    <div className="stack">
      <PageHead title={S.library.title} lede={S.library.lede}>
        <button type="button" className="btn" onClick={samples}>
          {S.library.samples}
        </button>
      </PageHead>
      <ErrorNote error={error ?? tree.error} onRetry={tree.reload} />
      {tree.data ? (
        <>
          <section className="card tight row between">
            <div style={{ minWidth: 0 }}>
              <div className="label">{S.library.folder}</div>
              <div className="mono small" style={{ overflowWrap: "anywhere" }}>
                {tree.data.root}
              </div>
              <div className="hint">{S.library.folderHint}</div>
            </div>
            <CopyButton text={tree.data.root} />
          </section>
          <Upload folders={allFolders(tree.data.tree)} onDone={tree.reload} />
          {docs.length === 0 ? (
            <Empty title={S.library.empty}>
              <p className="muted">{S.library.emptyHint}</p>
              <button type="button" className="btn accent" onClick={samples}>
                {S.library.samples}
              </button>
            </Empty>
          ) : (
            <div className="library-layout">
              <nav className="card tight tree" aria-label={S.library.folder}>
                <ul>
                  <li>
                    <button type="button" aria-current={folder === null} onClick={() => setFolder(null)}>
                      <Icon name="library" size={15} />
                      <span className="grow">{S.library.all}</span>
                      <span className="xsmall muted">{docs.length}</span>
                    </button>
                    <Tree folder={tree.data.tree} current={folder} onPick={setFolder} />
                  </li>
                </ul>
              </nav>
              <div className="stack tight">
                <input
                  className="input"
                  type="search"
                  placeholder={S.library.filter}
                  aria-label={S.library.filter}
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                />
                <ul className="doc-list">
                  {shown.map((doc) => (
                    <DocRow key={doc.id} doc={doc} act={act} remove={remove} />
                  ))}
                </ul>
              </div>
            </div>
          )}
        </>
      ) : tree.loading ? (
        <Spinner />
      ) : null}
    </div>
  );
}

function DocRow({
  doc,
  act,
  remove,
}: {
  doc: Doc;
  act: (work: () => Promise<unknown>, message?: string) => Promise<void>;
  remove: (doc: Doc) => Promise<void>;
}) {
  const settled = SETTLED.has(doc.status);
  const share = doc.progress.total ? Math.min(1, doc.progress.done / doc.progress.total) : null;
  const meta = [
    doc.folder || null,
    doc.pages ? plural(doc.pages, S.library.page, S.library.pages) : null,
    doc.status === "ready" ? plural(doc.chunks, S.library.passage, S.library.passages) : null,
    fmtBytes(doc.size),
  ].filter(Boolean);
  return (
    <li className="doc">
      <span className={`doc-type ${doc.type}`} aria-hidden="true">
        {doc.type.toUpperCase()}
      </span>
      <div className="stack tight" style={{ gap: "0.2rem", minWidth: 0 }}>
        <div className="row" style={{ gap: "0.5rem" }}>
          <span className="name">{doc.name}</span>
          <StatusBadge status={doc.status} />
          <span className="sr-only">{doc.label}</span>
        </div>
        <div className="meta">{meta.join(" · ")}</div>
        {!settled ? (
          <div className="stack tight" style={{ gap: "0.2rem" }}>
            <div
              className="progress"
              role="progressbar"
              aria-label={doc.progress.message || S.library.status[doc.status]}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={share === null ? undefined : Math.round(share * 100)}
            >
              <span style={{ width: share === null ? "15%" : `${Math.max(3, share * 100)}%` }} />
            </div>
            <span className="xsmall muted">{doc.progress.message}</span>
          </div>
        ) : null}
        {doc.status === "failed" && doc.error ? <span className="small" style={{ color: "var(--danger)" }}>{doc.error.message}</span> : null}
      </div>
      <div className="row" style={{ gap: "0.35rem", justifyContent: "flex-end" }}>
        {doc.type === "csv" && doc.status === "ready" ? (
          <Link className="btn small" href={`/analysis/?doc=${encodeURIComponent(doc.id)}`}>
            <Icon name="analysis" size={14} />
            {S.library.analyse}
          </Link>
        ) : null}
        <button type="button" className="btn small ghost" onClick={() => act(() => openDocument(doc.id))}>
          <Icon name="external" size={14} />
          {S.library.openFile}
        </button>
        {settled ? (
          <button type="button" className="btn small ghost" onClick={() => act(() => api(`/api/documents/${doc.id}/reread`, { method: "POST" }))}>
            <Icon name="refresh" size={14} />
            {S.library.readAgain}
          </button>
        ) : (
          <button type="button" className="btn small ghost" onClick={() => act(() => api(`/api/documents/${doc.id}/cancel`, { method: "POST" }))}>
            <Icon name="stop" size={14} />
            {S.library.cancel}
          </button>
        )}
        <button type="button" className="btn small ghost icon" aria-label={`${S.common.delete} ${doc.name}`} onClick={() => remove(doc)}>
          <Icon name="trash" size={15} />
        </button>
      </div>
    </li>
  );
}
