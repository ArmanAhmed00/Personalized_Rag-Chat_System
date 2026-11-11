"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError } from "@/lib/api";
import { useEscape } from "@/lib/hooks";
import { S, t } from "@/lib/strings";
import type { Status } from "@/lib/types";

import { Icon } from "./Icon";

export function PageHead({ title, lede, children }: { title: string; lede?: string; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {lede ? <p>{lede}</p> : null}
      </div>
      {children ? <div className="row">{children}</div> : null}
    </header>
  );
}

export function Spinner({ label = S.common.loading }: { label?: string }) {
  return (
    <span role="status" className="row muted small">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
}

export function ErrorNote({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return (
    <div className="note danger" role="alert">
      <p>{message}</p>
      {requestId ? (
        <p className="xsmall">
          {S.errors.requestId}: <span className="mono">{requestId}</span>
        </p>
      ) : null}
      {onRetry ? (
        <button type="button" className="btn small" onClick={onRetry}>
          {S.errors.retry}
        </button>
      ) : null}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      {children}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <label className="toggle">
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (index: number) => {
    const next = (index + tabs.length) % tabs.length;
    const tab = tabs[next];
    if (tab) {
      onChange(tab.id);
      refs.current[next]?.focus();
    }
  };
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, i) => (
        <button
          key={tab.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="tab"
          aria-selected={value === tab.id}
          tabIndex={value === tab.id ? 0 : -1}
          onClick={() => onChange(tab.id)}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight") move(i + 1);
            if (e.key === "ArrowLeft") move(i - 1);
          }}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { id: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" aria-pressed={value === o.id} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

const STATUS_CLASS: Record<Status, string> = {
  queued: "",
  reading: "teal",
  chunking: "teal",
  embedding: "teal",
  ready: "good",
  failed: "danger",
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge ${STATUS_CLASS[status]}`}>{S.library.status[status]}</span>;
}

export function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="tile">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {sub ? <span className="sub">{sub}</span> : null}
    </div>
  );
}

export function CopyButton({ text, label = S.common.copy }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn small"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard unavailable: the text stays visible to copy by hand */
        }
      }}
    >
      <Icon name={done ? "check" : "copy"} size={14} />
      {done ? S.common.copied : label}
    </button>
  );
}

export function Modal({
  title,
  children,
  onClose,
  actions,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  actions: ReactNode;
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEscape(onClose);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("button, input, select, textarea")?.focus();
    return () => previous?.focus();
  }, []);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} className="modal" role="dialog" aria-modal="true" aria-labelledby={id}>
        <h2 id={id}>{title}</h2>
        <div className="stack">{children}</div>
        <div className="row end">{actions}</div>
      </div>
    </div>
  );
}

/** "Are you sure?" as a promise: ``if (await confirm(text)) ...`` */
type Confirm = (message: string, options?: { danger?: boolean; yes?: string }) => Promise<boolean>;
type Notify = (message: string) => void;

const Feedback = createContext<{ confirm: Confirm; notify: Notify }>({
  confirm: async () => false,
  notify: () => undefined,
});

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [ask, setAsk] = useState<{ message: string; danger?: boolean; yes?: string; resolve: (v: boolean) => void } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const confirm = useCallback<Confirm>(
    (message, options) => new Promise((resolve) => setAsk({ message, ...options, resolve })),
    [],
  );
  const notify = useCallback<Notify>((message) => {
    setToast(message);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 4000);
  }, []);
  const answer = (value: boolean) => {
    ask?.resolve(value);
    setAsk(null);
  };

  return (
    <Feedback.Provider value={{ confirm, notify }}>
      {children}
      {ask ? (
        <Modal
          title={S.common.confirm}
          onClose={() => answer(false)}
          actions={
            <>
              <button type="button" className="btn" onClick={() => answer(false)}>
                {S.common.cancel}
              </button>
              <button type="button" className={`btn ${ask.danger ? "danger" : "primary"}`} onClick={() => answer(true)}>
                {ask.yes ?? S.common.yes}
              </button>
            </>
          }
        >
          <p>{ask.message}</p>
        </Modal>
      ) : null}
      <div aria-live="polite" className="sr-only">
        {toast}
      </div>
      {toast ? <div className="toast">{toast}</div> : null}
    </Feedback.Provider>
  );
}

export function useFeedback() {
  return useContext(Feedback);
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
    </label>
  );
}

export function plural(n: number, one: string, many: string): string {
  return t(n === 1 ? one : many, { n: n.toLocaleString() });
}
