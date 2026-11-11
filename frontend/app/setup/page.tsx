"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useConfig } from "@/components/Shell";
import { CopyButton, ErrorNote, Field, PageHead, Spinner, useFeedback } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi, usePageTitle } from "@/lib/hooks";
import { S } from "@/lib/strings";

interface Detect {
  library: string;
  default_library: string;
  library_locked: boolean;
  ollama: { url: string; reachable: boolean; models: string[] };
  recommended_model: string;
  pull_command: string;
  llm: string;
  llm_model: string;
}

interface Test {
  ok: boolean;
  message: string;
  models: string[];
}

export default function Setup() {
  usePageTitle(S.setup.title);
  const { reload } = useConfig();
  const { notify } = useFeedback();
  const detect = useApi<Detect>("/api/setup/detect");
  const [library, setLibrary] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [openaiUrl, setOpenaiUrl] = useState("http://127.0.0.1:1234/v1");
  const [openaiModel, setOpenaiModel] = useState("");
  const [test, setTest] = useState<Test | null>(null);
  const [samplesDone, setSamplesDone] = useState<string | null>(null);

  useEffect(() => {
    if (detect.data && !library) setLibrary(detect.data.library || detect.data.default_library);
  }, [detect.data, library]);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const saveLibrary = () =>
    run(async () => {
      await api("/api/setup", { json: { library } });
      await reload();
      notify(S.setup.folderSaved);
      detect.reload();
    });

  const chooseModel = (changes: Record<string, unknown>) =>
    run(async () => {
      await api("/api/settings", { method: "PUT", json: { changes } });
      await reload();
      notify(S.settings.savedNotes);
      detect.reload();
    });

  const testServer = () =>
    run(async () => {
      setTest(await api<Test>("/api/settings/test", { json: { changes: { llm: "openai", openai_url: openaiUrl, llm_model: openaiModel || "local" } } }));
    });

  const samples = () =>
    run(async () => {
      const out = await api<{ message: string }>("/api/library/samples", { method: "POST" });
      setSamplesDone(out.message);
    });

  const d = detect.data;
  return (
    <div className="stack" style={{ maxWidth: "48rem" }}>
      <PageHead title={S.setup.title} lede={S.setup.lede} />
      <ErrorNote error={error ?? detect.error} />
      {!d ? (
        <Spinner />
      ) : (
        <ol className="steps">
          <li>
            <section className="card stack">
              <h2>{S.setup.libraryTitle}</h2>
              <p className="muted">{S.setup.libraryBody}</p>
              <Field label={S.setup.libraryLabel} hint={d.library_locked ? S.setup.libraryLocked : undefined}>
                <input className="input mono" value={library} onChange={(e) => setLibrary(e.target.value)} disabled={d.library_locked} spellCheck={false} />
              </Field>
              <div className="row end">
                <button type="button" className="btn primary" onClick={saveLibrary} disabled={busy || d.library_locked || !library.trim()}>
                  {S.setup.useFolder}
                </button>
              </div>
            </section>
          </li>
          <li>
            <section className="card stack">
              <h2>{S.setup.modelTitle}</h2>
              <p className="muted">{S.setup.modelBody}</p>
              {d.ollama.reachable ? (
                <div className="stack tight">
                  <p className="note good">
                    {S.setup.ollamaFound} <span className="mono xsmall">{d.ollama.url}</span>
                  </p>
                  {d.ollama.models.length ? (
                    <>
                      <span className="label">{S.setup.ollamaModels}</span>
                      <ul className="doc-list">
                        {d.ollama.models.map((m) => (
                          <li key={m} className="row between">
                            <span className="mono">{m}</span>
                            {d.llm === "ollama" && d.llm_model === m ? (
                              <span className="badge good">{S.common.saved}</span>
                            ) : (
                              <button type="button" className="btn small" disabled={busy} onClick={() => chooseModel({ llm: "ollama", llm_model: m })}>
                                {S.setup.useModel}
                              </button>
                            )}
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : (
                    <div className="stack tight">
                      <p>{S.setup.noModels}</p>
                      <div className="row">
                        <code>{d.pull_command}</code>
                        <CopyButton text={d.pull_command} />
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="stack tight">
                  <p className="note warn">{S.setup.ollamaMissing}</p>
                  <p>{S.setup.ollamaInstall}</p>
                  <div className="row">
                    <code>{d.pull_command}</code>
                    <CopyButton text={d.pull_command} />
                  </div>
                </div>
              )}
              <div className="row">
                <button type="button" className="btn small" onClick={detect.reload}>
                  {S.setup.recheck}
                </button>
              </div>
              <hr />
              <p className="small">{S.setup.orOpenAI}</p>
              <div className="grid two">
                <Field label={S.setup.serverUrl}>
                  <input className="input mono" value={openaiUrl} onChange={(e) => setOpenaiUrl(e.target.value)} spellCheck={false} />
                </Field>
                <Field label={S.setup.modelName}>
                  <input className="input mono" value={openaiModel} onChange={(e) => setOpenaiModel(e.target.value)} spellCheck={false} />
                </Field>
              </div>
              {test ? (
                <p className={`note ${test.ok ? "good" : "danger"}`} role="status">
                  {test.message}
                </p>
              ) : null}
              <div className="row end">
                <button type="button" className="btn" onClick={testServer} disabled={busy || !openaiUrl.trim()}>
                  {S.setup.test}
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || !openaiUrl.trim() || !openaiModel.trim()}
                  onClick={() => chooseModel({ llm: "openai", openai_url: openaiUrl.trim(), llm_model: openaiModel.trim() })}
                >
                  {S.setup.useServer}
                </button>
              </div>
            </section>
          </li>
          <li>
            <section className="card stack">
              <h2>{S.setup.samplesTitle}</h2>
              <p className="muted">{S.setup.samplesBody}</p>
              {samplesDone ? <p className="note good">{samplesDone}</p> : null}
              <div className="row">
                <button type="button" className="btn accent" onClick={samples} disabled={busy || samplesDone !== null}>
                  {S.setup.samples}
                </button>
                <Link className="btn" href="/library/">
                  {S.setup.skip}
                </Link>
              </div>
            </section>
          </li>
        </ol>
      )}
    </div>
  );
}
