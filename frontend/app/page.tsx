"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { DemoAnswer } from "@/components/DemoAnswer";
import { Icon } from "@/components/Icon";
import { ErrorNote, useFeedback } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtAgo } from "@/lib/format";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S } from "@/lib/strings";
import type { Chat, ChatSummary, Doc } from "@/lib/types";

export default function Home() {
  usePageTitle("");
  const router = useRouter();
  const { notify } = useFeedback();
  const docs = useApi<{ documents: Doc[] }>("/api/documents", 5000);
  const chats = useApi<{ chats: ChatSummary[] }>("/api/chats");
  const [question, setQuestion] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const list = docs.data?.documents ?? [];
  const ready = list.filter((d) => d.status === "ready").length;
  const failed = list.filter((d) => d.status === "failed").length;
  const reading = list.length - ready - failed;

  const samples = async () => {
    setAdding(true);
    try {
      const out = await api<{ message: string }>("/api/library/samples", { method: "POST" });
      notify(out.message);
      router.push("/library/");
    } catch (err) {
      setError(err);
    } finally {
      setAdding(false);
    }
  };

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const q = question.trim();
    if (!q) return;
    try {
      const chat = await api<Chat>("/api/chats", { json: {} });
      router.push(`/chat/?id=${encodeURIComponent(chat.id)}&ask=${encodeURIComponent(q)}`);
    } catch (err) {
      setError(err);
    }
  };

  return (
    <div className="stack" style={{ gap: "var(--space-6)" }}>
      <section className="hero">
        <div className="stack">
          <h1>{S.home.title}</h1>
          <p className="lede">{S.home.lede}</p>
          <p className="row small muted" style={{ flexWrap: "nowrap" }}>
            <Icon name="lock" size={16} />
            {S.home.privacy}
          </p>
          <div className="row">
            <button type="button" className="btn accent" onClick={samples} disabled={adding}>
              {adding ? S.home.addingSamples : S.home.trySamples}
            </button>
            <Link className="btn" href="/library/">
              {S.home.addOwn}
            </Link>
          </div>
          <ErrorNote error={error} />
        </div>
        <DemoAnswer />
      </section>

      <form className="card stack tight" onSubmit={ask}>
        <label htmlFor="home-ask" className="label">
          {S.home.askLabel}
        </label>
        <div className="row">
          <input
            id="home-ask"
            className="input grow"
            placeholder={S.home.askPlaceholder}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={4000}
          />
          <button className="btn primary" type="submit" disabled={!question.trim()}>
            <Icon name="send" size={16} />
            {S.home.ask}
          </button>
        </div>
      </form>

      <div className="grid two">
        <section className="card">
          <div className="card-head">
            <h3>{S.home.recent}</h3>
            <Link className="btn small ghost" href="/chat/">
              {S.chat.newChat}
            </Link>
          </div>
          {chats.data?.chats.length ? (
            <ul className="doc-list">
              {chats.data.chats.slice(0, 5).map((c) => (
                <li key={c.id}>
                  <Link className="row between" href={`/chat/?id=${encodeURIComponent(c.id)}`}>
                    <span>{c.title}</span>
                    <span className="xsmall muted">{fmtAgo(c.updated_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{S.home.noChats}</p>
          )}
        </section>
        <section className="card">
          <div className="card-head">
            <h3>{S.home.libraryStatus}</h3>
            <Link className="btn small ghost" href="/library/">
              {S.common.open}
            </Link>
          </div>
          <div className="grid tiles">
            <div className="tile">
              <span className="label">{S.home.ready}</span>
              <span className="value">{ready}</span>
            </div>
            <div className="tile">
              <span className="label">{S.home.reading}</span>
              <span className="value">{reading}</span>
            </div>
            <div className="tile">
              <span className="label">{S.home.failed}</span>
              <span className="value">{failed}</span>
            </div>
          </div>
        </section>
      </div>

      <section className="card">
        <h3>{S.home.howTitle}</h3>
        <ol className="steps">
          {S.home.how.map((step) => (
            <li key={step}>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
