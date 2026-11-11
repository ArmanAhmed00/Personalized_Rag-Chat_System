"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { CitedText } from "@/components/CitedText";
import { Icon } from "@/components/Icon";
import { PassagePanel } from "@/components/PassagePanel";
import { ResultView } from "@/components/ResultView";
import { ErrorNote, Modal, Spinner, Toggle, useFeedback } from "@/components/ui";
import { ApiError, api, download, streamEvents } from "@/lib/api";
import { fmtAgo } from "@/lib/format";
import { useApi, usePageTitle, useQueryParam, useReducedMotion } from "@/lib/hooks";
import { S, t } from "@/lib/strings";
import type { Chat, ChatMessage, ChatSummary, Doc, SavedSource } from "@/lib/types";

function blankMessage(role: "user" | "assistant", content: string): ChatMessage {
  return {
    id: `pending-${role}-${Date.now()}`,
    role,
    content,
    created_at: Date.now() / 1000,
    status: role === "assistant" ? "streaming" : "complete",
    sources: [],
    cited: [],
    flags: [],
    query: null,
    usage: null,
    timings: {},
    feedback: null,
    suggestion: null,
    computed: null,
    error: null,
  };
}

export default function ChatPage() {
  const [id, setId] = useQueryParam("id");
  const [ask, setAsk] = useQueryParam("ask");
  const { confirm, notify } = useFeedback();
  const reduced = useReducedMotion();
  const list = useApi<{ chats: ChatSummary[] }>("/api/chats");
  const docs = useApi<{ documents: Doc[] }>("/api/documents");
  const prefs = useApi<{ suggestions: { id: string }[] }>("/api/preferences");
  const [chat, setChat] = useState<Chat | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState<AbortController | null>(null);
  const [panel, setPanel] = useState<{ source: SavedSource } | null>(null);
  const [hover, setHover] = useState<{ message: string; n: number } | null>(null);
  const [modal, setModal] = useState<"rename" | "docs" | null>(null);
  const loadedFor = useRef<string | null>(null);
  const asked = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  usePageTitle(chat?.title ?? S.chat.title);

  const load = useCallback(async (chatId: string) => {
    setLoading(true);
    try {
      const value = await api<Chat>(`/api/chats/${encodeURIComponent(chatId)}`);
      setChat(value);
      setError(null);
    } catch (err) {
      setChat(null);
      setError(err instanceof ApiError && err.status === 404 ? new Error(S.chat.notFound) : err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!id) {
      loadedFor.current = null;
      setChat(null);
      setError(null);
      return;
    }
    if (loadedFor.current === id) return;
    loadedFor.current = id;
    void load(id);
  }, [id, load]);

  const patchLast = (fn: (m: ChatMessage) => ChatMessage) =>
    setChat((c) => {
      if (!c || !c.messages.length) return c;
      const messages = [...c.messages];
      messages[messages.length - 1] = fn(messages[messages.length - 1]!);
      return { ...c, messages };
    });

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || streaming) return;
      let target = chat;
      try {
        if (!target) {
          target = await api<Chat>("/api/chats", { json: {} });
          loadedFor.current = target.id;
          setId(target.id, true);
        }
      } catch (err) {
        setError(err);
        return;
      }
      const chatId = target.id;
      setDraft("");
      setChat({ ...target, messages: [...target.messages, blankMessage("user", question), blankMessage("assistant", "")] });
      const controller = new AbortController();
      setStreaming(controller);
      try {
        for await (const event of streamEvents(`/api/chats/${encodeURIComponent(chatId)}/ask`, { question }, controller.signal)) {
          const data = event.data;
          if (event.event === "sources") {
            patchLast((m) => ({ ...m, sources: (data.sources as SavedSource[]) ?? [], query: (data.query as string) ?? m.query }));
          } else if (event.event === "delta") {
            patchLast((m) => ({ ...m, content: m.content + String(data.text ?? "") }));
          } else if (event.event === "done") {
            patchLast(() => data.message as ChatMessage);
          } else if (event.event === "error") {
            patchLast((m) => ({ ...m, status: "error", error: { code: String(data.code), message: String(data.message) } }));
          }
        }
      } catch (err) {
        if ((err as Error).name === "AbortError") {
          patchLast((m) => ({ ...m, status: "interrupted" }));
        } else {
          patchLast((m) => ({ ...m, status: "error", error: { code: "error", message: err instanceof Error ? err.message : S.errors.generic } }));
        }
      } finally {
        setStreaming(null);
        // The server has the final version (title, flags, interrupted text): show that.
        setTimeout(() => {
          void load(chatId);
          list.reload();
          prefs.reload();
        }, 250);
      }
    },
    [chat, streaming, setId, load, list, prefs],
  );

  // Arrived from the home page with a question: ask it once.
  useEffect(() => {
    if (ask && chat && !loading && asked.current !== ask && chat.id === id) {
      asked.current = ask;
      setAsk(null, true);
      void send(ask);
    }
  }, [ask, chat, loading, id, send, setAsk]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "end" });
  }, [chat?.messages.length, reduced]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void send(draft);
  };
  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send(draft);
    }
  };

  const remove = async () => {
    if (!chat || !(await confirm(S.chat.deleteConfirm, { danger: true, yes: S.common.delete }))) return;
    try {
      await api(`/api/chats/${chat.id}`, { method: "DELETE" });
      notify(S.chat.deleted);
      setId(null);
      list.reload();
    } catch (err) {
      setError(err);
    }
  };

  const feedback = async (message: ChatMessage, value: "up" | "down") => {
    if (!chat) return;
    const next = message.feedback === value ? null : value;
    try {
      const saved = await api<ChatMessage>(`/api/chats/${chat.id}/messages/${message.id}/feedback`, { method: "PUT", json: { value: next } });
      setChat((c) => (c ? { ...c, messages: c.messages.map((m) => (m.id === saved.id ? saved : m)) } : c));
    } catch (err) {
      notify(err instanceof Error ? err.message : S.errors.generic);
    }
  };

  const suggestion = async (sid: string, save: boolean) => {
    try {
      if (save) await api(`/api/preferences/suggestions/${sid}/save`, { method: "POST" });
      else await api(`/api/preferences/suggestions/${sid}`, { method: "DELETE" });
      if (save) notify(S.chat.suggestionSaved);
      prefs.reload();
    } catch (err) {
      notify(err instanceof Error ? err.message : S.errors.generic);
    }
  };

  const pending = useMemo(() => new Set((prefs.data?.suggestions ?? []).map((s) => s.id)), [prefs.data]);
  const ready = (docs.data?.documents ?? []).filter((d) => d.status === "ready");
  const lastQuestion = [...(chat?.messages ?? [])].reverse().find((m) => m.role === "user")?.content;

  return (
    <div className="chat-layout">
      <nav className="chat-list" aria-label={S.chat.chats}>
        <button
          type="button"
          className="btn primary"
          onClick={() => {
            setId(null);
            setDraft("");
          }}
        >
          <Icon name="plus" size={16} />
          {S.chat.newChat}
        </button>
        {list.data?.chats.length ? (
          <ul>
            {list.data.chats.map((c) => (
              <li key={c.id}>
                <Link href={`/chat/?id=${encodeURIComponent(c.id)}`} aria-current={c.id === id ? "page" : undefined} title={`${c.title} · ${fmtAgo(c.updated_at)}`}>
                  {c.title}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="xsmall muted">{S.chat.noChats}</p>
        )}
      </nav>
      <section className="conversation" aria-label={chat?.title ?? S.chat.title}>
        {chat ? (
          <div className="conversation-head">
            <h1 style={{ fontSize: "var(--text-lg)", margin: 0 }} title={chat.title}>
              {chat.title}
            </h1>
            <button type="button" className="btn small" onClick={() => setModal("docs")}>
              <Icon name="library" size={14} />
              {chat.documents ? t(S.chat.someDocs, { n: chat.documents.length }) : S.chat.allDocs}
            </button>
            <button type="button" className="btn small ghost" onClick={() => setModal("rename")}>
              {S.common.rename}
            </button>
            <ExportMenu chat={chat} />
            <button type="button" className="btn small ghost icon" aria-label={S.common.delete} onClick={remove} disabled={streaming !== null}>
              <Icon name="trash" size={15} />
            </button>
          </div>
        ) : null}
        <div className="messages" aria-live="polite" aria-busy={streaming !== null}>
          <ErrorNote error={error} />
          {loading && !chat ? <Spinner /> : null}
          {!chat && !loading ? (
            <div className="empty">
              <h2>{S.chat.empty}</h2>
              <p className="muted">{S.chat.emptyHint}</p>
            </div>
          ) : null}
          {chat?.messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="message user">
                {m.content}
              </div>
            ) : (
              <AssistantMessage
                key={m.id}
                message={m}
                active={hover?.message === m.id ? hover.n : null}
                onHover={(n) => setHover(n === null ? null : { message: m.id, n })}
                onOpen={(source) => setPanel({ source })}
                onFeedback={(v) => feedback(m, v)}
                suggestionPending={m.suggestion ? pending.has(m.suggestion.id) : false}
                onSuggestion={suggestion}
                onRetry={lastQuestion ? () => send(lastQuestion) : undefined}
                busy={streaming !== null}
              />
            ),
          )}
          <div ref={bottom} />
        </div>
        <div className="composer">
          <form onSubmit={submit}>
            <textarea
              aria-label={S.chat.placeholder}
              placeholder={S.chat.placeholder}
              value={draft}
              rows={Math.min(6, Math.max(1, draft.split("\n").length))}
              maxLength={4000}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKey}
            />
            {streaming ? (
              <button type="button" className="btn danger" onClick={() => streaming.abort()}>
                <Icon name="stop" size={16} />
                {S.chat.stop}
              </button>
            ) : (
              <button type="submit" className="btn primary" disabled={!draft.trim()}>
                <Icon name="send" size={16} />
                {S.chat.send}
              </button>
            )}
          </form>
        </div>
      </section>
      {panel ? <PassagePanel passage={panel.source} n={panel.source.n} onClose={() => setPanel(null)} /> : null}
      {modal === "rename" && chat ? (
        <RenameModal
          chat={chat}
          onClose={() => setModal(null)}
          onSaved={(c) => {
            setChat(c);
            list.reload();
            notify(S.chat.renamed);
          }}
        />
      ) : null}
      {modal === "docs" && chat ? <DocumentsModal chat={chat} docs={ready} onClose={() => setModal(null)} onSaved={setChat} /> : null}
    </div>
  );
}

function AssistantMessage({
  message: m,
  active,
  onHover,
  onOpen,
  onFeedback,
  suggestionPending,
  onSuggestion,
  onRetry,
  busy,
}: {
  message: ChatMessage;
  active: number | null;
  onHover: (n: number | null) => void;
  onOpen: (source: SavedSource) => void;
  onFeedback: (value: "up" | "down") => void;
  suggestionPending: boolean;
  onSuggestion: (id: string, save: boolean) => void;
  onRetry?: () => void;
  busy: boolean;
}) {
  const byN = new Map(m.sources.map((s) => [s.n, s]));
  const valid = new Set(m.sources.map((s) => s.n));
  const cited = m.cited.length ? m.sources.filter((s) => m.cited.includes(s.n)) : m.sources;
  const streaming = m.status === "streaming";
  const pendingId = m.id.startsWith("pending-");

  if (streaming && !m.content && !m.computed) {
    return (
      <div className="message assistant">
        <span className="small muted typing">{S.chat.thinking}</span>
      </div>
    );
  }

  return (
    <article className="message assistant">
      {m.computed ? (
        <div className="card stack">
          <span className="badge teal" style={{ justifySelf: "start" }}>
            {t(S.chat.computed, { name: m.computed.document ?? "" })}
          </span>
          <ResultView result={m.computed.result} summary={m.content} fixes={m.computed.fixes} />
          <p className="xsmall muted" style={{ margin: 0 }}>
            {m.computed.summary_source === "model" ? S.analysis.summaryModel : S.analysis.summaryComputed}
          </p>
          {m.flags.includes("summary_replaced") ? <p className="note warn">{S.chat.summaryReplaced}</p> : null}
        </div>
      ) : (
        <CitedText
          text={m.content}
          valid={valid}
          active={active}
          streaming={streaming}
          onHover={onHover}
          onCite={(n) => {
            const s = byN.get(n);
            if (s) onOpen(s);
          }}
        />
      )}
      {m.suggestion && suggestionPending ? (
        <div className="note row between">
          <span>{S.chat.suggestion}</span>
          <span className="row">
            <button type="button" className="btn small primary" onClick={() => onSuggestion(m.suggestion!.id, true)}>
              {S.chat.saveSuggestion}
            </button>
            <button type="button" className="btn small" onClick={() => onSuggestion(m.suggestion!.id, false)}>
              {S.chat.dismiss}
            </button>
          </span>
        </div>
      ) : null}
      {!m.computed && cited.length ? (
        <div className="stack tight">
          <span className="xsmall muted">{S.chat.sources}</span>
          <div className="sources">
            {cited.map((s) => (
              <button
                key={s.n}
                type="button"
                className="source-chip"
                onClick={() => onOpen(s)}
                onMouseEnter={() => onHover(s.n)}
                onMouseLeave={() => onHover(null)}
                style={active === s.n ? { borderColor: "var(--highlight-ink)" } : undefined}
              >
                <span className={`cite${active === s.n ? " active" : ""}`} aria-hidden="true">
                  {s.n}
                </span>
                <span>{s.label || s.document}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {m.flags.includes("no_citations") ? <p className="note warn">{S.chat.noCitation}</p> : null}
      {m.flags.includes("removed_citations") ? <p className="xsmall muted">{S.chat.removedCitations}</p> : null}
      {m.status === "interrupted" || m.flags.includes("interrupted") ? <p className="xsmall muted">{S.chat.interrupted}</p> : null}
      {m.status === "error" && m.error ? (
        <div className="note danger" role="alert">
          <p>{m.error.message}</p>
          {onRetry ? (
            <button type="button" className="btn small" onClick={onRetry} disabled={busy}>
              {S.chat.retry}
            </button>
          ) : null}
        </div>
      ) : null}
      {!streaming && !pendingId && m.status !== "error" ? (
        <div className="message-tools">
          <button
            type="button"
            className="btn small ghost icon"
            aria-label={S.chat.thumbsUp}
            aria-pressed={m.feedback === "up"}
            onClick={() => onFeedback("up")}
            style={m.feedback === "up" ? { color: "var(--good)" } : undefined}
          >
            <Icon name="thumbUp" size={15} />
          </button>
          <button
            type="button"
            className="btn small ghost icon"
            aria-label={S.chat.thumbsDown}
            aria-pressed={m.feedback === "down"}
            onClick={() => onFeedback("down")}
            style={m.feedback === "down" ? { color: "var(--danger)" } : undefined}
          >
            <Icon name="thumbDown" size={15} />
          </button>
          {m.query && !m.computed ? <span>{t(S.chat.rewritten, { query: m.query })}</span> : null}
        </div>
      ) : null}
    </article>
  );
}

function ExportMenu({ chat }: { chat: Chat }) {
  const { notify } = useFeedback();
  const [open, setOpen] = useState(false);
  const get = async (format: "md" | "json" | "pdf") => {
    setOpen(false);
    try {
      await download(`/api/chats/${chat.id}/export?format=${format}`, `${chat.title}.${format}`);
    } catch (err) {
      notify(err instanceof Error ? err.message : S.errors.generic);
    }
  };
  return (
    <div style={{ position: "relative" }}>
      <button type="button" className="btn small ghost" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen((v) => !v)}>
        <Icon name="download" size={14} />
        {S.chat.export}
      </button>
      {open ? (
        <div className="card tight stack tight" style={{ position: "absolute", right: 0, top: "110%", zIndex: 20, minWidth: "9rem" }}>
          {(["md", "json", "pdf"] as const).map((f) => (
            <button key={f} type="button" className="btn small ghost" style={{ justifyContent: "flex-start" }} onClick={() => get(f)}>
              {f === "md" ? S.chat.exportMd : f === "json" ? S.chat.exportJson : S.chat.exportPdf}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function RenameModal({ chat, onClose, onSaved }: { chat: Chat; onClose: () => void; onSaved: (c: Chat) => void }) {
  const [title, setTitle] = useState(chat.title);
  const [error, setError] = useState<unknown>(null);
  const save = async () => {
    try {
      onSaved(await api<Chat>(`/api/chats/${chat.id}`, { method: "PATCH", json: { title: title.trim() } }));
      onClose();
    } catch (err) {
      setError(err);
    }
  };
  return (
    <Modal
      title={S.chat.rename}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {S.common.cancel}
          </button>
          <button type="button" className="btn primary" onClick={save} disabled={!title.trim()}>
            {S.common.save}
          </button>
        </>
      }
    >
      <label className="field">
        <span>{S.chat.titleLabel}</span>
        <input
          className="input"
          value={title}
          maxLength={120}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void save()}
        />
      </label>
      <ErrorNote error={error} />
    </Modal>
  );
}

function DocumentsModal({ chat, docs, onClose, onSaved }: { chat: Chat; docs: Doc[]; onClose: () => void; onSaved: (c: Chat) => void }) {
  const [all, setAll] = useState(chat.documents === null);
  const [chosen, setChosen] = useState<Set<string>>(new Set(chat.documents ?? []));
  const [error, setError] = useState<unknown>(null);
  const save = async () => {
    try {
      const body = all || chosen.size === 0 ? { all_documents: true } : { documents: [...chosen] };
      onSaved(await api<Chat>(`/api/chats/${chat.id}`, { method: "PATCH", json: body }));
      onClose();
    } catch (err) {
      setError(err);
    }
  };
  return (
    <Modal
      title={S.chat.chooseDocs}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {S.common.cancel}
          </button>
          <button type="button" className="btn primary" onClick={save}>
            {S.chat.applyDocs}
          </button>
        </>
      }
    >
      <Toggle checked={all} onChange={setAll} label={S.chat.allDocs} />
      <p className="xsmall muted" style={{ margin: 0 }}>
        {S.chat.allDocsHint}
      </p>
      {!all ? (
        docs.length ? (
          <fieldset className="stack tight" style={{ border: 0, padding: 0, margin: 0, maxHeight: "18rem", overflowY: "auto" }}>
            <legend className="sr-only">{S.chat.documents}</legend>
            {docs.map((d) => (
              <label key={d.id} className="row small">
                <input
                  type="checkbox"
                  checked={chosen.has(d.id)}
                  onChange={(e) => {
                    const next = new Set(chosen);
                    if (e.target.checked) next.add(d.id);
                    else next.delete(d.id);
                    setChosen(next);
                  }}
                />
                <span>{d.path}</span>
              </label>
            ))}
          </fieldset>
        ) : (
          <p className="muted">{S.chat.noReadyDocs}</p>
        )
      ) : null}
      <ErrorNote error={error} />
    </Modal>
  );
}
