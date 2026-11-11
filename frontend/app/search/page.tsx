"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Highlight } from "@/components/Highlight";
import { Icon } from "@/components/Icon";
import { PassagePanel } from "@/components/PassagePanel";
import { Empty, ErrorNote, PageHead, Segmented, Spinner, Toggle } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtMs, sourceLabel } from "@/lib/format";
import { useApi, usePageTitle, useQueryParam } from "@/lib/hooks";
import { S, t } from "@/lib/strings";
import type { Doc, SearchHit } from "@/lib/types";

type Mode = "best" | "meaning" | "words";

interface Result {
  query: string;
  mode: Mode;
  hits: SearchHit[];
  notes: string[];
  timings: Record<string, number>;
  reranked: boolean;
  searched_passages: number;
}

export default function Search() {
  usePageTitle(S.search.title);
  const [q, setQ] = useQueryParam("q");
  const docs = useApi<{ documents: Doc[] }>("/api/documents");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<Mode>("best");
  const [rerank, setRerank] = useState(true);
  const [doc, setDoc] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<SearchHit | null>(null);
  const started = useRef<string | null>(null);

  const run = async (text: string) => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api<Result>("/api/search", { json: { query: text, mode, rerank, limit: 20, documents: doc ? [doc] : null } }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (q && started.current !== q) {
      started.current = q;
      setQuery(q);
      void run(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    started.current = query.trim();
    setQ(query.trim() || null, true);
    void run(query);
  };

  const ready = (docs.data?.documents ?? []).filter((d) => d.status === "ready");

  return (
    <div className="stack">
      <PageHead title={S.search.title} lede={S.search.lede} />
      <form className="card stack" onSubmit={submit} role="search">
        <div className="row">
          <input
            className="input grow"
            type="search"
            placeholder={S.search.placeholder}
            aria-label={S.search.placeholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={2000}
            autoFocus
          />
          <button className="btn primary" type="submit" disabled={busy || !query.trim()}>
            <Icon name="search" size={16} />
            {S.search.submit}
          </button>
        </div>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <Segmented
            label={S.search.mode}
            value={mode}
            onChange={setMode}
            options={(["best", "meaning", "words"] as Mode[]).map((m) => ({ id: m, label: S.search.modes[m] }))}
          />
          <Toggle checked={rerank} onChange={setRerank} label={S.search.rerank} disabled={mode === "words"} />
          <label className="row small">
            <span>{S.search.in}</span>
            <select className="select" value={doc} onChange={(e) => setDoc(e.target.value)} style={{ width: "auto", maxWidth: "18rem" }}>
              <option value="">{S.common.allDocuments}</option>
              {ready.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </form>
      <ErrorNote error={error} />
      {busy ? <Spinner /> : null}
      {result && !busy ? (
        <section className="stack" aria-live="polite">
          <p className="small muted">
            {t(S.search.searched, { count: result.searched_passages.toLocaleString(), time: fmtMs(result.timings.total_ms) })}
          </p>
          {result.notes.map((n) => (
            <p key={n} className="note">
              {n}
            </p>
          ))}
          {result.hits.length === 0 ? (
            <Empty title={S.search.none}>
              <p className="muted">{S.search.hint}</p>
            </Empty>
          ) : (
            <ol className="doc-list">
              {result.hits.map((hit) => (
                <li key={`${hit.doc_id}-${hit.i}`} className="card tight stack tight">
                  <div className="row between">
                    <div className="row" style={{ gap: "0.5rem", minWidth: 0 }}>
                      <span className="badge">#{hit.rank}</span>
                      <strong style={{ overflowWrap: "anywhere" }}>{sourceLabel(hit)}</strong>
                    </div>
                    <button type="button" className="btn small" onClick={() => setOpen(hit)}>
                      {S.search.open}
                    </button>
                  </div>
                  <p className="prose small" style={{ margin: 0 }}>
                    <Highlight text={(hit.text ?? "").slice(0, 420) + ((hit.text ?? "").length > 420 ? "…" : "")} words={hit.matched} />
                  </p>
                  <p className="xsmall muted" style={{ margin: 0 }}>
                    {hit.explanation}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </section>
      ) : null}
      {open ? <PassagePanel passage={open} n={open.rank} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}
