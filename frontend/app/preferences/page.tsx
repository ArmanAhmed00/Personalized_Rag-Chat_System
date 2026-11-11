"use client";

import { useState, type FormEvent } from "react";

import { Icon } from "@/components/Icon";
import { Empty, ErrorNote, PageHead, Spinner, Toggle, useFeedback } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtAgo } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S } from "@/lib/strings";

interface Item {
  id: string;
  text: string;
  created_at: number;
  updated_at: number;
}

interface Prefs {
  enabled: boolean;
  items: Item[];
  suggestions: { id: string; text: string; chat_id: string | null; created_at: number }[];
}

export default function Preferences() {
  usePageTitle(S.preferences.title);
  const { confirm, notify } = useFeedback();
  const prefs = useApi<Prefs>("/api/preferences");
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [error, setError] = useState<unknown>(null);

  const act = async (work: () => Promise<unknown>, message?: string) => {
    setError(null);
    try {
      const out = await work();
      if (out && typeof out === "object" && "enabled" in out) prefs.setData(out as Prefs);
      else prefs.reload();
      if (message) notify(message);
    } catch (err) {
      setError(err);
    }
  };

  const add = (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    void act(() => api("/api/preferences/items", { json: { text: text.trim() } }), S.common.saved).then(() => setText(""));
  };

  const clear = async () => {
    if (await confirm(S.preferences.clearConfirm, { danger: true, yes: S.preferences.clear })) {
      await act(() => api("/api/preferences/items", { method: "DELETE" }));
    }
  };

  const p = prefs.data;
  return (
    <div className="stack" style={{ maxWidth: "48rem" }}>
      <PageHead title={S.preferences.title} lede={S.preferences.lede}>
        {p?.items.length ? (
          <button type="button" className="btn danger small" onClick={clear}>
            {S.preferences.clear}
          </button>
        ) : null}
      </PageHead>
      <ErrorNote error={error ?? prefs.error} onRetry={prefs.reload} />
      {!p ? (
        <Spinner />
      ) : (
        <>
          <section className="card">
            <Toggle checked={p.enabled} onChange={(v) => act(() => api("/api/preferences", { method: "PUT", json: { enabled: v } }))} label={S.preferences.enabled} />
          </section>
          {p.suggestions.length ? (
            <section className="card stack tight">
              <h3>{S.preferences.suggestions}</h3>
              <ul className="doc-list">
                {p.suggestions.map((s) => (
                  <li key={s.id} className="row between">
                    <span>“{s.text}”</span>
                    <span className="row">
                      <button type="button" className="btn small primary" onClick={() => act(() => api(`/api/preferences/suggestions/${s.id}/save`, { method: "POST" }), S.chat.suggestionSaved)}>
                        {S.chat.saveSuggestion}
                      </button>
                      <button type="button" className="btn small" onClick={() => act(() => api(`/api/preferences/suggestions/${s.id}`, { method: "DELETE" }))}>
                        {S.chat.dismiss}
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <form className="card row" onSubmit={add}>
            <input
              className="input grow"
              placeholder={S.preferences.addPlaceholder}
              aria-label={S.preferences.add}
              value={text}
              maxLength={300}
              onChange={(e) => setText(e.target.value)}
            />
            <button type="submit" className="btn primary" disabled={!text.trim()}>
              <Icon name="plus" size={16} />
              {S.preferences.add}
            </button>
          </form>
          {p.items.length === 0 ? (
            <Empty title={S.preferences.empty}>
              <p className="muted">{S.preferences.emptyHint}</p>
            </Empty>
          ) : (
            <ul className="doc-list">
              {p.items.map((item) => (
                <li key={item.id} className="doc" style={{ gridTemplateColumns: "minmax(0,1fr) auto" }}>
                  {editing?.id === item.id ? (
                    <form
                      className="row"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void act(() => api(`/api/preferences/items/${item.id}`, { method: "PATCH", json: { text: editing.text.trim() } }), S.common.saved).then(() => setEditing(null));
                      }}
                    >
                      <input className="input grow" value={editing.text} maxLength={300} autoFocus aria-label={S.common.edit} onChange={(e) => setEditing({ id: item.id, text: e.target.value })} />
                      <button type="submit" className="btn small primary" disabled={!editing.text.trim()}>
                        {S.common.save}
                      </button>
                      <button type="button" className="btn small" onClick={() => setEditing(null)}>
                        {S.common.cancel}
                      </button>
                    </form>
                  ) : (
                    <div>
                      <div>{item.text}</div>
                      <div className="xsmall muted">{fmtAgo(item.updated_at)}</div>
                    </div>
                  )}
                  {editing?.id !== item.id ? (
                    <div className="row">
                      <button type="button" className="btn small ghost" onClick={() => setEditing({ id: item.id, text: item.text })}>
                        {S.common.edit}
                      </button>
                      <button type="button" className="btn small ghost icon" aria-label={`${S.common.delete}: ${item.text}`} onClick={() => act(() => api(`/api/preferences/items/${item.id}`, { method: "DELETE" }))}>
                        <Icon name="trash" size={15} />
                      </button>
                    </div>
                  ) : (
                    <span />
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
