"use client";

import { useRef, useState, type DragEvent } from "react";

import { api, ApiError } from "@/lib/api";
import { S, t } from "@/lib/strings";

import { Icon } from "./Icon";
import { plural, useFeedback } from "./ui";

const ACCEPT = [".pdf", ".epub", ".csv"];

/** Drag and drop (or choose) PDF, EPUB and CSV files into a library folder. */
export function Upload({ folders, onDone }: { folders: string[]; onDone: () => void }) {
  const { notify } = useFeedback();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [folder, setFolder] = useState("");
  const [problems, setProblems] = useState<string[]>([]);

  const send = async (files: File[]) => {
    const problems: string[] = [];
    let added = 0;
    for (const file of files) {
      if (!ACCEPT.some((ext) => file.name.toLowerCase().endsWith(ext))) {
        problems.push(t(S.library.unsupported, { name: file.name }));
        continue;
      }
      setBusy(t(S.library.uploading, { name: file.name }));
      const form = new FormData();
      form.append("file", file, file.name);
      if (folder) form.append("folder", folder);
      try {
        await api("/api/documents", { form });
        added += 1;
      } catch (err) {
        problems.push(t(S.library.uploadFailed, { name: file.name, message: err instanceof ApiError ? err.message : S.errors.generic }));
      }
    }
    setBusy(null);
    setProblems(problems);
    if (added) notify(t(S.library.uploaded, { count: plural(added, "{n} file", "{n} files") }));
    onDone();
  };

  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    void send(Array.from(event.dataTransfer.files));
  };

  return (
    <div className="stack tight">
      <div
        className={`dropzone${over ? " over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={drop}
      >
        <Icon name="upload" size={22} />
        <p style={{ margin: 0 }}>
          <strong>{S.library.drop}</strong> {S.library.dropOr}{" "}
          <button type="button" className="btn small" onClick={() => input.current?.click()} disabled={busy !== null}>
            {S.library.choose}
          </button>
        </p>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT.join(",")}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            void send(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        {folders.length > 0 ? (
          <label className="row small">
            <span>{S.library.uploadTo}</span>
            <select className="select" value={folder} onChange={(e) => setFolder(e.target.value)} style={{ width: "auto" }}>
              <option value="">{S.library.rootFolder}</option>
              {folders.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {busy ? (
          <p role="status" className="small muted" style={{ margin: 0 }}>
            {busy}
          </p>
        ) : null}
      </div>
      {problems.length ? (
        <div className="note warn" role="alert">
          {problems.map((p) => (
            <p key={p}>{p}</p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
