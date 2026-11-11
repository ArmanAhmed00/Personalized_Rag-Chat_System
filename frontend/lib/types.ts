// Shapes returned by the Outlawz API. Only the fields the UI uses are typed.

export type Status = "queued" | "reading" | "chunking" | "embedding" | "ready" | "failed";

export interface Config {
  app: string;
  version: string;
  library: string;
  model: { llm: string; llm_model: string; embedder: string; embed_model: string; reranker: string };
  cloud: boolean;
  token_required: boolean;
  needs_setup: boolean;
  warnings: { code: string; message: string }[];
}

export interface Doc {
  id: string;
  path: string;
  name: string;
  folder: string;
  type: string;
  label: string;
  size: number;
  status: Status;
  progress: { step: string; done: number; total: number; message: string };
  error: { code: string; message: string } | null;
  chunks: number;
  pages: number | null;
  added_at: number;
  ready_at: number | null;
  running: boolean;
  embed_model: string | null;
}

export interface Passage {
  doc_id: string;
  document: string;
  type?: string;
  i: number;
  text: string | null;
  kind?: string;
  page?: number | null;
  page_end?: number | null;
  section?: string | null;
  chapter?: string | null;
  rows?: number[] | null;
  label?: string;
  deleted?: boolean;
}

export interface SearchHit extends Passage {
  path: string;
  rank: number;
  ranks: Record<string, number>;
  matched: string[];
  moved_from: number | null;
  explanation: string;
  score: number;
}

export interface SavedSource extends Passage {
  n: number;
  label: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: number;
  status: "complete" | "interrupted" | "error" | "streaming";
  sources: SavedSource[];
  cited: number[];
  flags: string[];
  query: string | null;
  usage: { prompt_tokens: number; completion_tokens: number } | null;
  timings: Record<string, number>;
  feedback: "up" | "down" | null;
  suggestion: { id: string; text: string } | null;
  computed: Computed | null;
  error: { code: string; message: string } | null;
}

export interface Chat {
  id: string;
  title: string;
  titled: boolean;
  created_at: number;
  updated_at: number;
  documents: string[] | null;
  messages: ChatMessage[];
}

export interface ChatSummary {
  id: string;
  title: string;
  updated_at: number;
  messages: number;
  documents: string[] | null;
}

export type XKind = "category" | "ordered" | "number" | "time";

export interface ChartSpec {
  type: "bar" | "line" | "scatter" | "histogram" | "heatmap";
  title: string;
  x: string;
  y: string;
  series: string | null;
  value: string | null;
  x_kind: XKind;
  x_label: string;
  y_label: string;
  data: Record<string, unknown>[];
  notes: string[];
}

export interface Column {
  name: string;
  kind: string;
}

export interface AnalysisResult {
  op: string;
  title: string;
  columns: Column[];
  rows: unknown[][];
  summary: string;
  rows_used: number;
  rows_total: number;
  chart: ChartSpec | null;
  chart_note: string | null;
  stats: Record<string, unknown>;
  notes: string[];
  plan: Record<string, unknown>;
}

export interface Computed {
  doc_id?: string;
  document?: string;
  plan: Record<string, unknown>;
  result: AnalysisResult;
  summary: string;
  summary_source: "model" | "computed";
  rejected_numbers: string[];
  fixes: string[];
  attempts: number;
}

export interface ColumnCard {
  name: string;
  kind: string;
  filled: number;
  empty: number;
  empty_share: number;
  distinct: number;
  mean?: number;
  median?: number;
  min?: number | string;
  max?: number | string;
  histogram?: { rows: number; label?: string; from?: number; to?: number }[];
  top?: { value: string; rows: number }[];
}

export interface Overview {
  rows: number;
  columns: number;
  duplicates: number;
  empty_cells: number;
  empty_share: number;
  cards: ColumnCard[];
  findings: { kind: string; message: string; plan: Record<string, unknown> | null }[];
  correlations: { x: string; y: string; r: number }[];
  suggestions: { title: string; plan: Record<string, unknown> }[];
}

export interface Span {
  name: string;
  start_ms: number;
  ms: number;
}

export interface Trace {
  id: string;
  kind: string;
  t: number;
  total_ms: number;
  status: string;
  slow: boolean;
  code?: string;
  spans: Span[];
  ids?: Record<string, string>;
  counts?: Record<string, number>;
}

export interface Percentiles {
  p50: number | null;
  p95: number | null;
  p99?: number | null;
  mean?: number | null;
}

export interface ActivityEvent {
  seq: number;
  t: number;
  action: string;
  ids: Record<string, unknown>;
  hash: string;
}

export interface EvalRun {
  id: string;
  status: "running" | "done" | "failed";
  started: number;
  finished?: number;
  seconds?: number;
  suites?: string[];
  progress?: string;
  error?: string;
  settings?: Record<string, unknown>;
  results?: Record<string, Record<string, unknown>>;
  changes?: { metric: string; before: number; after: number; change: number; arrow: string; better: boolean }[];
  compare_to?: string | null;
  better?: number;
  worse?: number;
}

export interface SettingsResponse {
  settings: Record<string, unknown>;
  locked: string[];
  editable: string[];
  secrets: string[];
  cloud: boolean;
  notes?: string[];
}
