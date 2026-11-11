"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Icon } from "@/components/Icon";
import { ResultView } from "@/components/ResultView";
import { Empty, ErrorNote, Field, PageHead, Segmented, Spinner, Tabs, Tile } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { fmtNumber, fmtPercent } from "@/lib/format";
import { useApi, usePageTitle, useQueryParam } from "@/lib/hooks";
import { SERIES } from "@/lib/palette";
import { S, t } from "@/lib/strings";
import type { AnalysisResult, Column, ColumnCard, Doc, Overview } from "@/lib/types";

type Tab = "overview" | "ask" | "builder" | "compare";
type Plan = Record<string, unknown>;

interface Ran {
  result: AnalysisResult;
  fixes: string[];
  summary?: string;
  source?: "model" | "computed";
}

export default function Analysis() {
  usePageTitle(S.analysis.title);
  const [docParam, setDocParam] = useQueryParam("doc");
  const docs = useApi<{ documents: Doc[] }>("/api/documents");
  const tables = (docs.data?.documents ?? []).filter((d) => d.type === "csv" && d.status === "ready");
  const doc = tables.find((d) => d.id === docParam) ?? tables[0] ?? null;
  const [tab, setTab] = useState<Tab>("overview");
  const overview = useApi<{ columns: Column[]; overview: Overview }>(doc ? `/api/analysis/${doc.id}/overview` : null);
  const [ran, setRan] = useState<Ran | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [builderPlan, setBuilderPlan] = useState<Plan | null>(null);

  const runPlan = async (plan: Plan) => {
    if (!doc) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api<{ result: AnalysisResult; fixes: string[] }>("/api/analysis/run", { json: { doc_id: doc.id, plan } });
      setRan({ result: out.result, fixes: out.fixes });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const pendingRun = useRef<Plan | null>(null);
  useEffect(() => {
    setRan(null);
    setError(null);
    const plan = pendingRun.current;
    pendingRun.current = null;
    if (plan) void runPlan(plan);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc?.id, tab]);

  const look = (plan: Plan) => {
    setBuilderPlan(plan);
    pendingRun.current = plan;
    setTab("builder");
  };

  return (
    <div className="stack">
      <PageHead title={S.analysis.title} lede={S.analysis.lede} />
      <ErrorNote error={docs.error} onRetry={docs.reload} />
      {docs.loading && !docs.data ? <Spinner /> : null}
      {docs.data && !tables.length ? (
        <Empty title={S.analysis.noTables}>
          <Link className="btn" href="/library/">
            {S.nav.library}
          </Link>
        </Empty>
      ) : null}
      {doc ? (
        <>
          <div className="row between" style={{ flexWrap: "wrap" }}>
            <label className="row small">
              <span className="label">{S.analysis.choose}</span>
              <select className="select" value={doc.id} onChange={(e) => setDocParam(e.target.value, true)} style={{ width: "auto", maxWidth: "22rem" }}>
                {tables.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.path}
                  </option>
                ))}
              </select>
            </label>
            <Tabs
              label={S.analysis.title}
              value={tab}
              onChange={setTab}
              tabs={(["overview", "ask", "builder", "compare"] as Tab[]).map((id) => ({ id, label: S.analysis.tabs[id] }))}
            />
          </div>
          <div role="tabpanel" aria-label={S.analysis.tabs[tab]} className="stack">
            {tab === "overview" ? (
              <OverviewTab data={overview.data} error={overview.error} loading={overview.loading} onLook={look} />
            ) : null}
            {tab === "ask" ? <AskTab doc={doc} onResult={setRan} onError={setError} busy={busy} setBusy={setBusy} /> : null}
            {tab === "builder" ? (
              <Builder columns={overview.data?.columns ?? []} initial={builderPlan} onRun={runPlan} busy={busy} />
            ) : null}
            {tab === "compare" ? <CompareTab doc={doc} tables={tables} /> : null}
            <ErrorNote error={error} />
            {busy ? <Spinner /> : null}
            {ran && !busy ? (
              <section className="card">
                <ResultView result={ran.result} summary={ran.summary} fixes={ran.fixes} />
                {ran.source ? (
                  <p className="xsmall muted">{ran.source === "model" ? S.analysis.summaryModel : S.analysis.summaryComputed}</p>
                ) : null}
              </section>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ overview

function OverviewTab({
  data,
  error,
  loading,
  onLook,
}: {
  data: { columns: Column[]; overview: Overview } | null;
  error: unknown;
  loading: boolean;
  onLook: (plan: Plan) => void;
}) {
  if (error) return <ErrorNote error={error} />;
  if (loading || !data) return <Spinner />;
  const o = data.overview;
  return (
    <div className="stack">
      <div className="grid tiles">
        <Tile label={S.analysis.rows} value={fmtNumber(o.rows)} />
        <Tile label={S.analysis.columns} value={fmtNumber(o.columns)} />
        <Tile label={S.analysis.duplicates} value={fmtNumber(o.duplicates)} />
        <Tile label={S.analysis.emptyCells} value={fmtNumber(o.empty_cells)} sub={fmtPercent(o.empty_share, 1)} />
      </div>
      <div className="grid two">
        <section className="card stack tight">
          <h3>{S.analysis.findings}</h3>
          {o.findings.length ? (
            <ul className="doc-list">
              {o.findings.map((f, i) => (
                <li key={i} className="finding">
                  <span className="small">{f.message}</span>
                  {f.plan ? (
                    <button type="button" className="btn small" onClick={() => onLook(f.plan as Plan)}>
                      {S.analysis.lookAt}
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">{S.analysis.noFindings}</p>
          )}
        </section>
        <section className="card stack tight">
          <h3>{S.analysis.suggestions}</h3>
          <div className="row" style={{ flexWrap: "wrap" }}>
            {o.suggestions.map((s) => (
              <button key={s.title} type="button" className="btn small" onClick={() => onLook(s.plan)}>
                {s.title}
              </button>
            ))}
          </div>
          {o.correlations.length ? (
            <>
              <h3 style={{ marginTop: "var(--space-3)" }}>{S.analysis.correlations}</h3>
              <ul className="doc-list">
                {o.correlations.map((c) => (
                  <li key={`${c.x}-${c.y}`} className="stack tight" style={{ gap: "0.2rem" }}>
                    <button
                      type="button"
                      className="btn small ghost row between"
                      style={{ justifyContent: "space-between", width: "100%" }}
                      onClick={() => onLook({ op: "relationship", x: c.x, y: c.y })}
                    >
                      <span>
                        {c.x} × {c.y}
                      </span>
                      <span className="num">
                        {S.analysis.strength} = {fmtNumber(c.r, 2)}
                      </span>
                    </button>
                    <div className="share-bar" style={{ height: "0.4rem" }} aria-hidden="true">
                      <span style={{ width: `${Math.abs(c.r) * 100}%`, background: c.r >= 0 ? SERIES[0] : SERIES[7] }} />
                    </div>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
      </div>
      <h2>{S.analysis.columnCards}</h2>
      <div className="grid three">
        {o.cards.map((c) => (
          <ColumnCardView key={c.name} card={c} />
        ))}
      </div>
    </div>
  );
}

function ColumnCardView({ card }: { card: ColumnCard }) {
  const hist = card.histogram ?? [];
  const peak = Math.max(...hist.map((h) => h.rows), 1);
  // An id-like column (almost every value different) has no "most common" values worth showing.
  const top = card.distinct > Math.max(card.filled, 1) * 0.9 ? [] : card.top ?? [];
  const filled = Math.max(card.filled, 1);
  return (
    <section className="card tight stack tight">
      <div className="row between">
        <strong style={{ overflowWrap: "anywhere" }}>{card.name}</strong>
        <span className="badge">{S.analysis.kinds[card.kind as keyof typeof S.analysis.kinds] ?? card.kind}</span>
      </div>
      <p className="xsmall muted" style={{ margin: 0 }}>
        {t(S.analysis.distinct, { n: card.distinct.toLocaleString() })}
        {card.empty ? ` · ${t(S.analysis.gaps, { n: card.empty.toLocaleString() })} (${fmtPercent(card.empty_share, 1)})` : ""}
      </p>
      {card.kind === "number" ? (
        <p className="small" style={{ margin: 0 }}>
          <span className="num">{fmtNumber(card.min)}</span> – <span className="num">{fmtNumber(card.max)}</span> · x̄{" "}
          <span className="num">{fmtNumber(card.mean, 2)}</span> · med <span className="num">{fmtNumber(card.median, 2)}</span>
        </p>
      ) : null}
      {hist.length ? (
        <div className="minibars" role="img" aria-label={hist.map((h) => `${h.label ?? fmtNumber(h.from)}: ${h.rows}`).join(", ")}>
          {hist.map((h, i) => (
            <span key={i} style={{ height: `${(h.rows / peak) * 100}%` }} title={`${h.label ?? `${fmtNumber(h.from)} – ${fmtNumber(h.to)}`}: ${h.rows}`} />
          ))}
        </div>
      ) : null}
      {top.length ? (
        <ul className="stack tight" style={{ listStyle: "none", margin: 0, padding: 0, gap: "0.25rem" }}>
          {top.slice(0, 4).map((v) => (
            <li key={v.value} className="xsmall" style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: "0.5rem" }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v.value}</span>
              <span className="num muted">{fmtPercent(v.rows / filled)}</span>
              <span className="share-bar" style={{ height: "0.3rem", gridColumn: "1 / -1" }} aria-hidden="true">
                <span style={{ width: `${(v.rows / filled) * 100}%`, background: SERIES[0] }} />
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

// ------------------------------------------------------------------ ask

function AskTab({
  doc,
  onResult,
  onError,
  busy,
  setBusy,
}: {
  doc: Doc;
  onResult: (ran: Ran | null) => void;
  onError: (err: unknown) => void;
  busy: boolean;
  setBusy: (v: boolean) => void;
}) {
  const [question, setQuestion] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim()) return;
    setBusy(true);
    onError(null);
    onResult(null);
    try {
      const out = await api<{ result: AnalysisResult; fixes: string[]; summary: string; summary_source: "model" | "computed" }>("/api/analysis/ask", {
        json: { doc_id: doc.id, question },
      });
      onResult({ result: out.result, fixes: out.fixes, summary: out.summary, source: out.summary_source });
    } catch (err) {
      onError(err instanceof ApiError && err.code === "no_plan" ? new Error(err.message || S.analysis.noPlan) : err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="card row" onSubmit={submit}>
      <input
        className="input grow"
        placeholder={S.analysis.askPlaceholder}
        aria-label={S.analysis.askPlaceholder}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        maxLength={2000}
      />
      <button type="submit" className="btn primary" disabled={busy || !question.trim()}>
        {S.analysis.ask}
      </button>
    </form>
  );
}

// ------------------------------------------------------------------ builder

const OPS = ["aggregate", "compare_groups", "relationship", "correlation", "distribution", "crosstab", "outliers", "missing", "summary", "rows"] as const;
type Op = (typeof OPS)[number];
const AGGS = ["count", "sum", "mean", "median", "min", "max", "distinct", "std"] as const;
const FILTER_OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "between", "is_null", "not_null"] as const;

const DEFAULTS: Record<Op, Plan> = {
  aggregate: { op: "aggregate", agg: "count", by: [] },
  compare_groups: { op: "compare_groups", test: "auto" },
  relationship: { op: "relationship" },
  correlation: { op: "correlation", method: "pearson" },
  distribution: { op: "distribution", bins: 20 },
  crosstab: { op: "crosstab", normalize: "none" },
  outliers: { op: "outliers", method: "iqr", limit: 50 },
  missing: { op: "missing" },
  summary: { op: "summary" },
  rows: { op: "rows", descending: false, limit: 20 },
};

interface FilterRow {
  column: string;
  op: string;
  value: string;
}

function parseValue(op: string, raw: string): unknown {
  const one = (v: string) => {
    const text = v.trim();
    if (text !== "" && !Number.isNaN(Number(text))) return Number(text);
    if (text === "true" || text === "false") return text === "true";
    return text;
  };
  if (op === "is_null" || op === "not_null") return undefined;
  if (op === "in" || op === "not_in" || op === "between") return raw.split(",").map(one);
  return one(raw);
}

function Builder({ columns, initial, onRun, busy }: { columns: Column[]; initial: Plan | null; onRun: (plan: Plan) => void; busy: boolean }) {
  const [mode, setMode] = useState<"form" | "json">("form");
  const [plan, setPlan] = useState<Plan>(initial ?? DEFAULTS.aggregate);
  const [filters, setFilters] = useState<FilterRow[]>([]);
  const [json, setJson] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);

  useEffect(() => {
    if (!initial) return;
    setPlan(initial);
    const f = Array.isArray(initial.filters) ? (initial.filters as { column: string; op: string; value?: unknown }[]) : [];
    setFilters(f.map((x) => ({ column: x.column, op: x.op, value: Array.isArray(x.value) ? x.value.join(", ") : x.value === undefined || x.value === null ? "" : String(x.value) })));
  }, [initial]);

  const op = (plan.op as Op) ?? "aggregate";
  const set = (key: string, value: unknown) =>
    setPlan((p) => {
      const next = { ...p };
      if (value === "" || value === undefined || value === null) delete next[key];
      else next[key] = value;
      return next;
    });
  const full = (): Plan => {
    const out: Plan = { ...plan };
    const rows = filters.filter((f) => f.column && f.op);
    if (rows.length) out.filters = rows.map((f) => ({ column: f.column, op: f.op, ...(parseValue(f.op, f.value) === undefined ? {} : { value: parseValue(f.op, f.value) }) }));
    else delete out.filters;
    return out;
  };

  const names = columns.map((c) => c.name);
  const numbers = columns.filter((c) => c.kind === "number").map((c) => c.name);
  const dates = columns.filter((c) => c.kind === "date").map((c) => c.name);
  const categories = columns.filter((c) => c.kind === "text" || c.kind === "boolean").map((c) => c.name);

  const pick = (key: string, options: string[], optional = false): ReactNode => (
    <Field label={S.analysis.fields[key as keyof typeof S.analysis.fields] ?? key}>
      <select className="select" value={String(plan[key] ?? "")} onChange={(e) => set(key, e.target.value)}>
        <option value="">{optional ? S.analysis.noneOption : S.analysis.pickColumn}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </Field>
  );
  const choice = (key: string, options: readonly string[], labels: Record<string, string> = S.analysis.choices): ReactNode => (
    <Field label={S.analysis.fields[key as keyof typeof S.analysis.fields] ?? key}>
      <select className="select" value={String(plan[key] ?? options[0])} onChange={(e) => set(key, e.target.value)}>
        {options.map((o) => (
          <option key={o} value={o}>
            {labels[o] ?? o}
          </option>
        ))}
      </select>
    </Field>
  );
  const number = (key: string, min: number, max: number): ReactNode => (
    <Field label={S.analysis.fields[key as keyof typeof S.analysis.fields] ?? key}>
      <input className="input" type="number" min={min} max={max} value={plan[key] === undefined ? "" : String(plan[key])} onChange={(e) => set(key, e.target.value === "" ? "" : Number(e.target.value))} />
    </Field>
  );
  const by = (Array.isArray(plan.by) ? plan.by : []) as string[];

  const fields: Record<Op, ReactNode> = {
    aggregate: (
      <>
        {choice("agg", AGGS, S.analysis.aggs)}
        {plan.agg !== "count" ? pick("column", plan.agg === "distinct" ? names : numbers) : null}
        <Field label={S.analysis.fields.by}>
          <select className="select" value={by[0] ?? ""} onChange={(e) => set("by", e.target.value ? [e.target.value, ...by.slice(1)] : [])}>
            <option value="">{S.analysis.noneOption}</option>
            {[...categories, ...numbers].map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </Field>
        {by[0] ? (
          <Field label={S.analysis.fields.by2}>
            <select className="select" value={by[1] ?? ""} onChange={(e) => set("by", e.target.value ? [by[0], e.target.value] : [by[0]])}>
              <option value="">{S.analysis.noneOption}</option>
              {categories.filter((c) => c !== by[0]).map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {dates.length ? (
          <>
            <Field label={S.analysis.fields.period}>
              <select className="select" value={String(plan.period ?? "")} onChange={(e) => set("period", e.target.value)}>
                <option value="">{S.analysis.noneOption}</option>
                {(["day", "week", "month", "quarter", "year"] as const).map((o) => (
                  <option key={o} value={o}>
                    {S.analysis.choices[o]}
                  </option>
                ))}
              </select>
            </Field>
            {plan.period ? pick("date_column", dates) : null}
          </>
        ) : null}
        {choice("sort", ["value_desc", "value_asc", "label"])}
        {number("limit", 1, 1000)}
      </>
    ),
    compare_groups: (
      <>
        {pick("column", names)}
        {pick("group", categories)}
        {choice("test", ["auto", "t_test", "anova", "chi_square"])}
      </>
    ),
    relationship: (
      <>
        {pick("x", numbers)}
        {pick("y", numbers)}
        {pick("color", categories, true)}
      </>
    ),
    correlation: choice("method", ["pearson", "spearman"]),
    distribution: (
      <>
        {pick("column", numbers)}
        {number("bins", 2, 60)}
      </>
    ),
    crosstab: (
      <>
        {pick("rows", categories)}
        {pick("columns", categories)}
        {choice("normalize", ["none", "rows", "columns", "all"])}
      </>
    ),
    outliers: (
      <>
        {pick("column", numbers)}
        {choice("method", ["iqr", "zscore"])}
        {number("limit", 1, 500)}
      </>
    ),
    missing: null,
    summary: null,
    rows: (
      <>
        {pick("sort_by", names, true)}
        <Field label={S.analysis.fields.descending}>
          <select className="select" value={plan.descending ? "yes" : "no"} onChange={(e) => set("descending", e.target.value === "yes")}>
            <option value="no">{S.common.no}</option>
            <option value="yes">{S.common.yes}</option>
          </select>
        </Field>
        {number("limit", 1, 200)}
      </>
    ),
  };

  const switchMode = (next: "form" | "json") => {
    if (next === "json") setJson(JSON.stringify(full(), null, 2));
    else {
      try {
        const parsed = JSON.parse(json) as Plan;
        setPlan(parsed);
        setFilters([]);
      } catch {
        /* keep the form as it was */
      }
    }
    setJsonError(null);
    setMode(next);
  };

  const run = () => {
    if (mode === "json") {
      try {
        onRun(JSON.parse(json) as Plan);
        setJsonError(null);
      } catch (err) {
        setJsonError((err as Error).message);
      }
    } else onRun(full());
  };

  return (
    <section className="card stack">
      <div className="row between">
        <Segmented
          label={S.analysis.tabs.builder}
          value={mode}
          onChange={switchMode}
          options={[
            { id: "form", label: S.analysis.editForm },
            { id: "json", label: S.analysis.editJson },
          ]}
        />
      </div>
      {mode === "form" ? (
        <>
          <div className="grid three">
            <Field label={S.analysis.op}>
              <select className="select" value={op} onChange={(e) => setPlan({ ...DEFAULTS[e.target.value as Op] })}>
                {OPS.map((o) => (
                  <option key={o} value={o}>
                    {S.analysis.ops[o]}
                  </option>
                ))}
              </select>
            </Field>
            {fields[op]}
          </div>
          <fieldset className="stack tight" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="label">{S.analysis.filters}</legend>
            {filters.map((f, i) => (
              <div key={i} className="row" style={{ flexWrap: "wrap" }}>
                <select
                  className="select"
                  aria-label={S.analysis.fields.column}
                  value={f.column}
                  style={{ width: "auto" }}
                  onChange={(e) => setFilters(filters.map((x, k) => (k === i ? { ...x, column: e.target.value } : x)))}
                >
                  <option value="">{S.analysis.pickColumn}</option>
                  {names.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
                <select
                  className="select"
                  aria-label={S.analysis.op}
                  value={f.op}
                  style={{ width: "auto" }}
                  onChange={(e) => setFilters(filters.map((x, k) => (k === i ? { ...x, op: e.target.value } : x)))}
                >
                  {FILTER_OPS.map((o) => (
                    <option key={o} value={o}>
                      {S.analysis.filterOps[o]}
                    </option>
                  ))}
                </select>
                {f.op !== "is_null" && f.op !== "not_null" ? (
                  <input
                    className="input"
                    aria-label={S.analysis.value}
                    value={f.value}
                    style={{ width: "12rem" }}
                    onChange={(e) => setFilters(filters.map((x, k) => (k === i ? { ...x, value: e.target.value } : x)))}
                  />
                ) : null}
                <button type="button" className="btn small ghost icon" aria-label={S.analysis.removeFilter} onClick={() => setFilters(filters.filter((_, k) => k !== i))}>
                  <Icon name="close" size={14} />
                </button>
              </div>
            ))}
            <div className="row">
              <button type="button" className="btn small" onClick={() => setFilters([...filters, { column: "", op: "eq", value: "" }])} disabled={filters.length >= 10}>
                <Icon name="plus" size={14} />
                {S.analysis.addFilter}
              </button>
              <span className="xsmall muted">{S.analysis.valueHint}</span>
            </div>
          </fieldset>
        </>
      ) : (
        <>
          <textarea className="textarea code" rows={12} value={json} onChange={(e) => setJson(e.target.value)} spellCheck={false} aria-label={S.analysis.editJson} />
          {jsonError ? (
            <p className="note danger" role="alert">
              {jsonError}
            </p>
          ) : null}
        </>
      )}
      <div className="row end">
        <button type="button" className="btn primary" onClick={run} disabled={busy}>
          {S.analysis.run}
        </button>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ compare

interface Comparison {
  a: { name: string; rows: number; columns: number } | string;
  b: { name: string; rows: number; columns: number } | string;
  added: string[];
  removed: string[];
  type_changes: { column: string; from: string; to: string }[];
  numbers: { column: string; mean_a: number; mean_b: number; shift: number; shift_pct: number | null; std_diff: number | null; size: string }[];
  categories: { column: string; new: string[]; new_count: number; vanished: string[]; vanished_count: number }[];
  missing: { column: string; empty_a: number; empty_b: number; share_a: number; share_b: number }[];
  summary: string;
}

function CompareTab({ doc, tables }: { doc: Doc; tables: Doc[] }) {
  const others = tables.filter((d) => d.id !== doc.id);
  const [other, setOther] = useState(others[0]?.id ?? "");
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!others.length) return <p className="note">{S.analysis.noCompare}</p>;
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api<Comparison>("/api/analysis/compare", { json: { a: doc.id, b: other } }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  const list = (items: string[], total = items.length) =>
    items.length ? items.slice(0, 8).join(", ") + (total > 8 ? `, … (+${(total - 8).toLocaleString()})` : "") : S.common.none;
  return (
    <div className="stack">
      <section className="card row" style={{ flexWrap: "wrap" }}>
        <span className="small">{doc.name}</span>
        <span className="small muted">{S.analysis.compareWith}</span>
        <select className="select" value={other} onChange={(e) => setOther(e.target.value)} style={{ width: "auto" }} aria-label={S.analysis.compareWith}>
          {others.map((d) => (
            <option key={d.id} value={d.id}>
              {d.path}
            </option>
          ))}
        </select>
        <button type="button" className="btn primary" onClick={run} disabled={busy || !other}>
          {S.analysis.compare}
        </button>
      </section>
      <ErrorNote error={error} />
      {busy ? <Spinner /> : null}
      {data && !busy ? (
        <section className="card stack">
          <p>{data.summary}</p>
          <div className="grid three">
            <div>
              <h3>{S.analysis.added}</h3>
              <p className="small">{list(data.added)}</p>
            </div>
            <div>
              <h3>{S.analysis.removed}</h3>
              <p className="small">{list(data.removed)}</p>
            </div>
            <div>
              <h3>{S.analysis.typeChanges}</h3>
              <p className="small">{data.type_changes.length ? data.type_changes.map((c) => `${c.column} (${c.from} → ${c.to})`).join("; ") : S.common.none}</p>
            </div>
          </div>
          <h3>{S.analysis.shifts}</h3>
          {data.numbers.length ? (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">{S.analysis.fields.column}</th>
                    <th scope="col" className="n">
                      {S.analysis.meanShift}
                    </th>
                    <th scope="col" className="n">
                      {S.analysis.change}
                    </th>
                    <th scope="col">{S.analysis.size}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.numbers.map((n) => (
                    <tr key={n.column}>
                      <td>{n.column}</td>
                      <td className="num">
                        {fmtNumber(n.mean_a, 2)} → {fmtNumber(n.mean_b, 2)}
                      </td>
                      <td className="num">
                        {n.shift >= 0 ? "▲" : "▼"} {n.shift_pct === null ? fmtNumber(n.shift, 2) : `${fmtNumber(n.shift_pct, 1)}%`}
                      </td>
                      <td>{n.size}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="small muted">{S.analysis.nothingChanged}</p>
          )}
          <h3>{S.analysis.categories}</h3>
          {data.categories.length ? (
            <ul className="doc-list">
              {data.categories.map((c) => (
                <li key={c.column} className="small">
                  <strong>{c.column}</strong>: {S.analysis.newValues}: {list(c.new, c.new_count)} · {S.analysis.vanished}: {list(c.vanished, c.vanished_count)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="small muted">{S.analysis.nothingChanged}</p>
          )}
          <h3>{S.analysis.missingChanges}</h3>
          {data.missing.length ? (
            <ul className="doc-list">
              {data.missing.map((m) => (
                <li key={m.column} className="small">
                  <strong>{m.column}</strong>: {S.analysis.emptyBefore} {fmtNumber(m.empty_a)} ({fmtPercent(m.share_a, 1)}) → {S.analysis.emptyAfter} {fmtNumber(m.empty_b)} ({fmtPercent(m.share_b, 1)})
                </li>
              ))}
            </ul>
          ) : (
            <p className="small muted">{S.analysis.nothingChanged}</p>
          )}
        </section>
      ) : null}
    </div>
  );
}
