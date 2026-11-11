// The API client. Every request carries the custom X-Outlawz header (the CSRF guard) and, if the
// user entered one, the access token. The token is kept in memory only, never in storage.

import { S } from "./strings";

let token: string | null = null;

export function setToken(value: string | null): void {
  token = value && value.trim() ? value.trim() : null;
}

export function hasToken(): boolean {
  return token !== null;
}

/** Same origin in the built app; the API port when running the frontend dev server. */
export function apiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (configured) return configured.replace(/\/$/, "");
  if (typeof window !== "undefined" && window.location.port === "3000") {
    return `${window.location.protocol}//${window.location.hostname}:8642`;
  }
  return "";
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: Record<string, unknown>,
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function headers(extra?: Record<string, string>): Headers {
  const h = new Headers(extra);
  h.set("X-Outlawz", "1");
  if (token) h.set("X-Outlawz-Token", token);
  return h;
}

async function failure(res: Response): Promise<ApiError> {
  let error: ApiError;
  try {
    const body = (await res.json()) as {
      error?: { code?: string; message?: string; details?: Record<string, unknown>; request_id?: string };
    };
    const e = body.error ?? {};
    error = new ApiError(res.status, e.code ?? "error", e.message ?? S.errors.generic, e.details, e.request_id);
  } catch {
    error = new ApiError(res.status, "error", S.errors.generic);
  }
  // A missing or wrong token: the shell shows the token screen again.
  if (res.status === 401 && typeof window !== "undefined") {
    token = null;
    window.dispatchEvent(new Event("outlawz:token"));
  }
  return error;
}

interface Options {
  method?: string;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const hasBody = options.json !== undefined || options.form !== undefined;
  let res: Response;
  try {
    res = await fetch(apiBase() + path, {
      method: options.method ?? (hasBody ? "POST" : "GET"),
      headers: headers(options.json !== undefined ? { "content-type": "application/json" } : undefined),
      body: options.form ?? (options.json !== undefined ? JSON.stringify(options.json) : undefined),
      signal: options.signal,
      cache: "no-store",
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "offline", S.errors.offline);
  }
  if (!res.ok) throw await failure(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

/** POST and read Server-Sent Events (EventSource can't send a body or headers). */
export async function* streamEvents(
  path: string,
  json: unknown,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  let res: Response;
  try {
    res = await fetch(apiBase() + path, {
      method: "POST",
      headers: headers({ "content-type": "application/json", accept: "text/event-stream" }),
      body: JSON.stringify(json),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "offline", S.errors.offline);
  }
  if (!res.ok || !res.body) throw await failure(res);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) yield { event, data: JSON.parse(data.join("\n")) as Record<string, unknown> };
      cut = buffer.indexOf("\n\n");
    }
  }
}

function filenameFrom(disposition: string | null): string | null {
  if (!disposition) return null;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

async function fetchBlob(path: string): Promise<{ blob: Blob; name: string | null }> {
  let res: Response;
  try {
    res = await fetch(apiBase() + path, { headers: headers(), cache: "no-store" });
  } catch {
    throw new ApiError(0, "offline", S.errors.offline);
  }
  if (!res.ok) throw await failure(res);
  return { blob: await res.blob(), name: filenameFrom(res.headers.get("content-disposition")) };
}

/** Download through fetch, so the access token is sent too. */
export async function download(path: string, fallbackName: string): Promise<void> {
  const { blob, name } = await fetchBlob(path);
  saveBlob(blob, name ?? fallbackName);
}

export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** Open a document in a new tab (a PDF at the right page when given a fragment like #page=3). */
export async function openDocument(docId: string, fragment = ""): Promise<void> {
  const tab = window.open("", "_blank");
  try {
    const { blob } = await fetchBlob(`/api/documents/${encodeURIComponent(docId)}/file`);
    const url = URL.createObjectURL(blob) + fragment;
    if (tab) tab.location.href = url;
    else window.location.href = url;
  } catch (err) {
    tab?.close();
    throw err;
  }
}
