"use client";

import { useEffect, useRef } from "react";

import { openDocument } from "@/lib/api";
import { sourceLabel } from "@/lib/format";
import { useEscape } from "@/lib/hooks";
import { S } from "@/lib/strings";
import type { Passage } from "@/lib/types";

import { Icon } from "./Icon";
import { useFeedback } from "./ui";

/** The citation side panel: the exact passage, where it is, and a link to the original file. */
export function PassagePanel({
  passage,
  n,
  onClose,
}: {
  passage: Passage;
  n?: number;
  onClose: () => void;
}) {
  const { notify } = useFeedback();
  const closeRef = useRef<HTMLButtonElement>(null);
  useEscape(onClose);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => previous?.focus();
  }, [passage]);

  const where = sourceLabel(passage);
  const fragment = passage.type === "pdf" || /\.pdf$/i.test(passage.document) ? (passage.page ? `#page=${passage.page}` : "") : "";

  return (
    <aside className="side-panel" role="dialog" aria-modal="false" aria-labelledby="passage-title">
      <header>
        <div>
          <p className="xsmall muted" style={{ margin: 0 }}>
            {n ? `[${n}] ` : ""}
            {S.chat.passage}
          </p>
          <h2 id="passage-title" style={{ fontSize: "var(--text-md)", margin: 0 }}>
            {where}
          </h2>
        </div>
        <button ref={closeRef} type="button" className="btn ghost icon" aria-label={S.common.close} onClick={onClose}>
          <Icon name="close" />
        </button>
      </header>
      <div className="body">
        {passage.deleted || passage.text === null ? (
          <p className="note warn">{S.chat.deletedDoc}</p>
        ) : (
          <div className="passage">{passage.text}</div>
        )}
        {!passage.deleted ? (
          <div className="row">
            <button
              type="button"
              className="btn small"
              onClick={() => openDocument(passage.doc_id, fragment).catch((err: Error) => notify(err.message))}
            >
              <Icon name="external" size={14} />
              {S.chat.openDocument}
            </button>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
