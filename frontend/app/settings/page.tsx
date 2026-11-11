"use client";

import { useEffect, useMemo, useState } from "react";

import { useConfig } from "@/components/Shell";
import { ErrorNote, PageHead, Spinner, Toggle, useFeedback } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S, t } from "@/lib/strings";
import type { SettingsResponse } from "@/lib/types";

type Key = keyof typeof S.settings.fields;
type Kind = "text" | "number" | "secret" | "bool" | { options: string[] };

interface FieldDef {
  key: Key;
  kind: Kind;
  min?: number;
  max?: number;
  step?: number;
  when?: (values: Record<string, unknown>) => boolean;
}

const OFF = "off";

const GROUPS: { title: string; fields: FieldDef[]; test?: boolean }[] = [
  {
    title: S.settings.model,
    test: true,
    fields: [
      { key: "llm", kind: { options: ["ollama", "openai", "anthropic", "fake"] } },
      { key: "llm_model", kind: "text" },
      { key: "ollama_url", kind: "text", when: (v) => v.llm === "ollama" },
      { key: "openai_url", kind: "text", when: (v) => v.llm === "openai" },
      { key: "openai_key", kind: "secret", when: (v) => v.llm === "openai" },
      { key: "anthropic_key", kind: "secret", when: (v) => v.llm === "anthropic" },
      { key: "llm_timeout", kind: "number", min: 5, max: 1800 },
      { key: "temperature", kind: "number", min: 0, max: 2, step: 0.05 },
      { key: "max_answer_tokens", kind: "number", min: 64, max: 8192 },
    ],
  },
  {
    title: S.settings.search,
    fields: [
      { key: "embedder", kind: { options: ["fastembed", "ollama", "fake"] } },
      { key: "embed_model", kind: "text" },
      { key: "reranker", kind: { options: ["model", "lexical", OFF] } },
      { key: "rerank_model", kind: "text", when: (v) => v.reranker === "model" },
      { key: "top_k", kind: "number", min: 1, max: 50 },
      { key: "vector_k", kind: "number", min: 1, max: 500 },
      { key: "keyword_k", kind: "number", min: 1, max: 500 },
      { key: "rerank_k", kind: "number", min: 1, max: 200 },
      { key: "rrf_k", kind: "number", min: 1, max: 1000 },
      { key: "vector_weight", kind: "number", min: 0, max: 10, step: 0.1 },
      { key: "keyword_weight", kind: "number", min: 0, max: 10, step: 0.1 },
    ],
  },
  {
    title: S.settings.reading,
    fields: [
      { key: "max_upload_mb", kind: "number", min: 1, max: 2048 },
      { key: "workers", kind: "number", min: 1, max: 16 },
      { key: "retries", kind: "number", min: 0, max: 10 },
      { key: "chunk_tokens", kind: "number", min: 50, max: 2000 },
      { key: "chunk_overlap", kind: "number", min: 0, max: 500 },
      { key: "chunk_min", kind: "number", min: 1, max: 500 },
      { key: "csv_rows", kind: "number", min: 10, max: 1_000_000 },
      { key: "ocr", kind: { options: ["auto", OFF] } },
      { key: "watch_interval", kind: "number", min: 0.5, max: 600, step: 0.5 },
      { key: "watch_depth", kind: "number", min: 1, max: 32 },
    ],
  },
  {
    title: S.settings.chat,
    fields: [
      { key: "history_messages", kind: "number", min: 0, max: 40 },
      { key: "context_tokens", kind: "number", min: 200, max: 100_000 },
      { key: "preferences", kind: "bool" },
    ],
  },
  {
    title: S.settings.logs,
    fields: [
      { key: "log_days", kind: "number", min: 1, max: 3650 },
      { key: "activity_days", kind: "number", min: 7, max: 36500 },
      { key: "trace_sample", kind: "number", min: 0, max: 1, step: 0.05 },
      { key: "slow_trace_ms", kind: "number", min: 1 },
      { key: "backup_every", kind: { options: ["daily", OFF] } },
      { key: "backup_keep", kind: "number", min: 1, max: 365 },
      { key: "backup_documents", kind: "bool" },
      { key: "trash_days", kind: "number", min: 1, max: 3650 },
      { key: "export_days", kind: "number", min: 1, max: 3650 },
      { key: "log_level", kind: { options: ["debug", "info", "warning", "error"] } },
    ],
  },
  {
    title: S.settings.privacy,
    fields: [{ key: "update_check", kind: "bool" }],
  },
];

function optionLabel(key: Key, value: string): string {
  if (key === "llm") return S.settings.providers[value as keyof typeof S.settings.providers] ?? value;
  if (value === OFF) return S.common.off;
  if (value === "ollama") return S.settings.providers.ollama;
  if (value === "fake") return S.settings.providers.fake;
  return S.settings.options[value as keyof typeof S.settings.options] ?? value;
}

interface Update {
  enabled: boolean;
  current?: string;
  latest?: string | null;
  update?: boolean;
  checked?: boolean;
}

export default function Settings() {
  usePageTitle(S.settings.title);
  const { reload: reloadConfig, config } = useConfig();
  const { confirm, notify } = useFeedback();
  const loaded = useApi<SettingsResponse>("/api/settings");
  const update = useApi<Update>("/api/system/update");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [notes, setNotes] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (loaded.data) setValues({ ...loaded.data.settings, ...Object.fromEntries(loaded.data.secrets.map((k) => [k, ""])) });
  }, [loaded.data]);

  const changes = useMemo(() => {
    const out: Record<string, unknown> = {};
    if (!loaded.data) return out;
    for (const [k, v] of Object.entries(values)) {
      if (loaded.data.secrets.includes(k)) {
        if (typeof v === "string" && v.trim()) out[k] = v.trim();
      } else if (JSON.stringify(v) !== JSON.stringify(loaded.data.settings[k])) out[k] = v;
    }
    return out;
  }, [values, loaded.data]);
  const dirty = Object.keys(changes).length > 0;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await api<SettingsResponse>("/api/settings", { method: "PUT", json: { changes } });
      loaded.setData(out);
      setNotes(out.notes ?? []);
      notify(S.settings.savedNotes);
      await reloadConfig();
      update.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const testConnection = async () => {
    setTest(null);
    setBusy(true);
    try {
      const keys = ["llm", "llm_model", "ollama_url", "openai_url", "openai_key", "anthropic_key", "llm_timeout"];
      const candidate = Object.fromEntries(Object.entries(changes).filter(([k]) => keys.includes(k)));
      setTest(await api<{ ok: boolean; message: string }>("/api/settings/test", { json: { changes: candidate } }));
    } catch (err) {
      setTest({ ok: false, message: err instanceof Error ? err.message : S.errors.generic });
    } finally {
      setBusy(false);
    }
  };

  const rebuild = async () => {
    if (!(await confirm(S.settings.rebuildConfirm, { danger: true, yes: S.settings.rebuild }))) return;
    try {
      const out = await api<{ message: string }>("/api/library/rebuild", { method: "POST" });
      notify(out.message);
    } catch (err) {
      setError(err);
    }
  };

  if (!loaded.data) {
    return (
      <div className="stack">
        <PageHead title={S.settings.title} lede={S.settings.lede} />
        <ErrorNote error={loaded.error} onRetry={loaded.reload} />
        {loaded.loading ? <Spinner /> : null}
      </div>
    );
  }

  const data = loaded.data;
  const field = (f: FieldDef) => {
    if (f.when && !f.when(values)) return null;
    const [label, hint] = S.settings.fields[f.key];
    const locked = data.locked.includes(f.key);
    const value = values[f.key];
    const set = (v: unknown) => setValues((old) => ({ ...old, [f.key]: v }));
    const id = `setting-${f.key}`;
    const help = [hint, locked ? S.settings.locked : ""].filter(Boolean).join(" ");
    if (f.kind === "bool") {
      return (
        <div key={f.key} className="stack tight" style={{ gap: "0.2rem" }}>
          <Toggle checked={Boolean(value)} onChange={set} label={label} disabled={locked} />
          {help ? <span className="hint">{help}</span> : null}
        </div>
      );
    }
    let control;
    if (typeof f.kind === "object") {
      control = (
        <select id={id} className="select" value={String(value ?? "")} disabled={locked} onChange={(e) => set(e.target.value)}>
          {f.kind.options.map((o) => (
            <option key={o} value={o}>
              {optionLabel(f.key, o)}
            </option>
          ))}
        </select>
      );
    } else if (f.kind === "secret") {
      const isSet = Boolean(data.settings[f.key]);
      control = (
        <input
          id={id}
          className="input mono"
          type="password"
          autoComplete="off"
          placeholder={isSet ? "••••••••" : S.settings.secretPlaceholder}
          value={String(value ?? "")}
          disabled={locked}
          onChange={(e) => set(e.target.value)}
          aria-describedby={`${id}-hint`}
        />
      );
      return (
        <label key={f.key} className="field" htmlFor={id}>
          <span>{label}</span>
          {control}
          <span className="hint" id={`${id}-hint`}>
            {[isSet ? S.settings.keySet : S.settings.keyEmpty, help].filter(Boolean).join(" ")}
          </span>
        </label>
      );
    } else if (f.kind === "number") {
      control = (
        <input
          id={id}
          className="input"
          type="number"
          min={f.min}
          max={f.max}
          step={f.step ?? 1}
          value={value === undefined || value === null ? "" : String(value)}
          disabled={locked}
          onChange={(e) => set(e.target.value === "" ? "" : Number(e.target.value))}
        />
      );
    } else {
      control = <input id={id} className="input mono" value={String(value ?? "")} disabled={locked} spellCheck={false} onChange={(e) => set(e.target.value)} />;
    }
    return (
      <label key={f.key} className="field" htmlFor={id}>
        <span>{label}</span>
        {control}
        {help ? <span className="hint">{help}</span> : null}
      </label>
    );
  };

  return (
    <div className="stack" style={{ maxWidth: "56rem", paddingBottom: dirty ? "5rem" : undefined }}>
      <PageHead title={S.settings.title} lede={S.settings.lede} />
      <ErrorNote error={error} />
      {notes.length ? (
        <div className="note" role="status">
          {notes.map((n) => (
            <p key={n}>{n}</p>
          ))}
        </div>
      ) : null}
      {GROUPS.map((g) => (
        <section key={g.title} className="card stack">
          <h2 style={{ fontSize: "var(--text-lg)", margin: 0 }}>{g.title}</h2>
          <div className="grid two">{g.fields.map(field)}</div>
          {g.test ? (
            <div className="stack tight">
              {test ? (
                <p className={`note ${test.ok ? "good" : "danger"}`} role="status">
                  {test.message}
                </p>
              ) : null}
              <div className="row">
                <button type="button" className="btn" onClick={testConnection} disabled={busy}>
                  {S.settings.test}
                </button>
              </div>
            </div>
          ) : null}
          {g.title === S.settings.privacy ? (
            <p className="small muted" style={{ margin: 0 }}>
              {S.settings.version} {config?.version}
              {update.data?.enabled && update.data.checked ? (update.data.update ? ` · ${t(S.settings.update, { version: update.data.latest ?? "" })}` : ` · ${S.settings.upToDate}`) : ""}
            </p>
          ) : null}
        </section>
      ))}
      <section className="card stack tight">
        <h2 style={{ fontSize: "var(--text-lg)", margin: 0 }}>{S.settings.rebuild}</h2>
        <p className="small muted" style={{ margin: 0 }}>
          {S.settings.rebuildHint}
        </p>
        <div>
          <button type="button" className="btn danger" onClick={rebuild}>
            {S.settings.rebuild}
          </button>
        </div>
      </section>
      {dirty ? (
        <div className="savebar" role="region" aria-label={S.settings.unsaved}>
          <span>{S.settings.unsaved}</span>
          <span className="row">
            <button type="button" className="btn" onClick={() => setValues({ ...data.settings, ...Object.fromEntries(data.secrets.map((k) => [k, ""])) })}>
              {S.settings.discard}
            </button>
            <button type="button" className="btn primary" onClick={save} disabled={busy}>
              {S.common.save}
            </button>
          </span>
        </div>
      ) : null}
    </div>
  );
}
