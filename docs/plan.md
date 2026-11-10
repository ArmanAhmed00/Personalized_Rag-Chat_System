# Sidelamp: build plan

Sidelamp is a local, open-source app for chatting with your own PDFs, EPUBs and CSVs. Every answer
cites the passage it came from, and every number about a spreadsheet is computed with pandas.

- **Name:** Sidelamp. **Slug / package / CLI:** `sidelamp`. **Licence:** MIT.
- **Default port:** 8642 (on 127.0.0.1).
- **Library folder:** `~/Documents/Sidelamp Library`. **Hidden app folder inside it:** `.sidelamp/`.

## File tree

```
.
├── backend/
│   ├── pyproject.toml              # package, deps, ruff, mypy, pytest config
│   ├── sidelamp/
│   │   ├── __init__.py             # __version__
│   │   ├── __main__.py             # python -m sidelamp
│   │   ├── cli.py                  # start, doctor, backup, restore, verify, eval, rebuild
│   │   ├── config.py               # Settings (one typed class) + settings file + secrets
│   │   ├── paths.py                # OS data folder, library folder, app folder layout
│   │   ├── errors.py               # AppError + error codes (plain-language messages)
│   │   ├── app.py                  # FastAPI factory, lifespan, static site serving
│   │   ├── state.py                # the running app's services (one object, no globals)
│   │   ├── guard.py                # local-access guard (loopback/token, Host, CSRF)
│   │   ├── middleware.py           # request id, JSON errors, security headers, metrics hook
│   │   ├── store/
│   │   │   ├── files.py            # atomic JSON, JSONL append/read, atomic dirs, keyed locks
│   │   │   ├── lock.py             # single-instance lock (pid + heartbeat, stale detection)
│   │   │   ├── migrations.py       # format version + migration runner (backs up first)
│   │   │   └── safepath.py         # every path must resolve inside the library
│   │   ├── library/
│   │   │   ├── documents.py        # document index (documents.json) = the queue
│   │   │   ├── uploads.py          # content sniffing, size limit, "name (2).ext"
│   │   │   ├── trash.py            # move to Trash, purge after 30 days
│   │   │   └── watcher.py          # os.scandir polling, rename/move by sha256
│   │   ├── ingest/
│   │   │   ├── pipeline.py         # extract → chunk → embed → write atomically
│   │   │   ├── queue.py            # workers, retries with backoff, cancel
│   │   │   ├── chunker.py          # paragraph-aware, section-bounded
│   │   │   ├── embed.py            # embedder interface + fastembed / ollama / fake
│   │   │   └── extractors/
│   │   │       ├── base.py         # Extractor interface + registry
│   │   │       ├── pdf.py          # PyMuPDF: text, headings, tables, pages, OCR
│   │   │       ├── epub.py         # ebooklib + BeautifulSoup
│   │   │       └── csv.py          # schema, column profile, row blocks
│   │   ├── search/
│   │   │   ├── tokenizer.py        # decimals/codes kept whole, accent + plural folding
│   │   │   ├── bm25.py             # Okapi BM25 scoring
│   │   │   ├── index.py            # in-memory float32 matrix + inverted index, incremental
│   │   │   ├── fusion.py           # Reciprocal Rank Fusion
│   │   │   ├── rerank.py           # reranker interface + cross-encoder / lexical / off
│   │   │   └── service.py          # hybrid search with explanations and modes
│   │   ├── llm/
│   │   │   ├── base.py             # streaming provider interface + registry
│   │   │   ├── ollama.py
│   │   │   ├── openai.py           # any OpenAI-compatible server
│   │   │   ├── anthropic.py
│   │   │   └── fake.py             # deterministic provider for tests
│   │   ├── chat/
│   │   │   ├── prompts.py          # system prompt, sources block, rewrite prompt
│   │   │   ├── rewrite.py          # follow-up → standalone query
│   │   │   ├── citations.py        # server-side [n] check
│   │   │   ├── answer.py           # retrieval-augmented answering, streaming events
│   │   │   ├── chats.py            # one JSON file per chat
│   │   │   └── preferences.py      # suggestions, saved items, on/off
│   │   ├── analysis/
│   │   │   ├── plans.py            # strict Pydantic plan models (the fixed menu)
│   │   │   ├── validate.py         # check against real columns, "Did you mean…"
│   │   │   ├── run.py              # fixed pandas routines
│   │   │   ├── stats.py            # t, F, chi-square distributions (no scipy)
│   │   │   ├── charts.py           # chart spec builder + validator
│   │   │   ├── numbers.py          # every number in a summary must be computed
│   │   │   ├── profile.py          # automatic overview + findings
│   │   │   └── compare.py          # two CSVs side by side
│   │   ├── obs/
│   │   │   ├── metrics.py          # per-request and per-step numbers, percentiles
│   │   │   ├── traces.py           # timed waterfalls, sampling
│   │   │   └── activity.py         # SHA-256 hash-chained activity log
│   │   ├── evals/
│   │   │   ├── benchmark.py        # builds the synthetic benchmark (2 PDFs, 1 CSV, 24 questions)
│   │   │   └── suites.py           # chunking, retrieval, answers, data questions
│   │   ├── exports.py              # chat → MD/JSON/PDF, "Download everything" ZIP
│   │   ├── backups.py              # .tar.gz + manifest, verify, restore, schedule
│   │   ├── maintenance.py          # retention loop
│   │   ├── doctor.py               # environment checks with fixes
│   │   ├── samples/                # synthetic CSVs + short public-domain PDF and EPUB
│   │   ├── api/                    # one router per area (system, documents, search,
│   │   │                           #   chats, preferences, analysis, ops, evals, backups)
│   │   └── web/                    # built static site (copied in at build time, gitignored)
│   └── tests/                      # pytest, offline: temp folders + fake LLM + fake embedder
├── frontend/
│   ├── package.json, next.config.ts, tsconfig.json, eslint.config.mjs
│   ├── app/                        # App Router pages (static export)
│   ├── components/                 # shell, citations panel, charts (hand-written SVG), …
│   ├── lib/                        # api client, strings (all UI copy), formatting
│   └── styles/                     # tokens.css + plain CSS
├── scripts/                        # run.sh, run.ps1, loadtest.py, build_web.py
├── docker/                         # Dockerfile, compose.yaml
├── docs/                           # architecture.md, plan.md
├── .github/                        # CI, release, issue/PR templates
├── README.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, CHANGELOG.md, ROADMAP.md
├── LICENSE, .gitignore, .env.example, .pre-commit-config.yaml
```

## What lives where

**Library folder** (your documents, any subfolders) and inside it **`.sidelamp/`**:

```
.sidelamp/
├── format.json            # on-disk format version
├── lock                   # single-instance lock (pid, host, heartbeat)
├── documents.json         # document index: also the processing queue
├── docs/<doc_id>/         # passages.jsonl, vectors.npy, meta.json (written via temp dir + rename)
├── chats/<chat_id>.json   # one file per chat
├── preferences.json
├── settings.json          # settings saved from the UI
├── logs/                  # metrics-*.jsonl, traces-*.jsonl, activity-*.jsonl (numbers and ids only)
├── evals/                 # benchmark work area + saved runs
└── tmp/                   # staging for atomic writes (same disk, so renames are atomic)
```

**OS data folder** (`~/Library/Application Support/Sidelamp`, `%APPDATA%\Sidelamp`,
`~/.local/share/sidelamp`): `config.json` (which library to open), `secrets.json` (API keys, never
in the library so they don't sync), `models/`, `exports/`, `backups/`, `trash/`.

## Settings

One typed class. Order of precedence: **env var > `.sidelamp/settings.json` (saved by the UI) >
default**. Env vars use the `SIDELAMP_` prefix (`SIDELAMP_PORT=9000`). Settings set by an env var
show as locked in the UI.

| Setting | Default | What it does |
|---|---|---|
| **Folders and server** | | |
| `library` | `~/Documents/Sidelamp Library` | The folder with your documents |
| `data_dir` | OS data folder | Models, exports, backups, Trash |
| `host` | `127.0.0.1` | Address to listen on |
| `port` | `8642` | Port to listen on |
| `open_browser` | `true` | Open the browser on `sidelamp start` |
| **Access** | | |
| `access_token` | empty | If set, every API request must carry it (use from another device) |
| `allowed_hosts` | empty | Extra Host names to accept (besides localhost, 127.0.0.1, [::1]) |
| `dev_origins` | empty | Origins allowed to call the API cross-origin (frontend dev server only) |
| `docker` | auto | Running in Docker (port published on host loopback only) |
| **Library and reading** | | |
| `max_upload_mb` | `50` | Largest file you can upload |
| `watch_interval` | `3` | Seconds between library scans |
| `watch_depth` | `8` | Deepest subfolder level scanned |
| `workers` | `1` | Documents processed at the same time |
| `retries` | `3` | Retries for temporary errors (with backoff) |
| `csv_rows` | `5000` | CSV rows indexed for search (analysis always uses every row) |
| `chunk_tokens` / `chunk_overlap` / `chunk_min` | `350` / `50` / `40` | Passage size rules |
| `ocr` | `auto` | Read scanned pages if Tesseract is installed (`auto` or `off`) |
| **Models** | | |
| `embedder` | `fastembed` | `fastembed` or `ollama` |
| `embed_model` | `BAAI/bge-small-en-v1.5` | Changing it re-processes documents |
| `reranker` | `model` | `model`, `lexical` or `off` |
| `rerank_model` | `Xenova/ms-marco-MiniLM-L-6-v2` | Cross-encoder used by `model` |
| `llm` | `ollama` | `ollama`, `openai` (any compatible server) or `anthropic` |
| `llm_model` | `llama3.1:8b` | Model name for the chosen provider |
| `ollama_url` | `http://127.0.0.1:11434` | |
| `openai_url` | `http://127.0.0.1:1234/v1` | LM Studio, llama.cpp server, vLLM, or a cloud API |
| `openai_key` / `anthropic_key` | empty | Stored in `secrets.json`, never in the library |
| `llm_timeout` | `120` | Seconds before a model call gives up |
| `temperature` | `0.1` | |
| `max_answer_tokens` | `800` | |
| **Search** | | |
| `vector_k` / `keyword_k` | `40` / `40` | Candidates from each method |
| `rerank_k` | `30` | Candidates the reranker sees |
| `top_k` | `8` | Passages sent to the model in chat |
| `rrf_k` | `60` | RRF constant |
| `vector_weight` / `keyword_weight` | `1.0` / `1.0` | RRF weights |
| **Chat** | | |
| `history_messages` | `6` | Messages used to rewrite a follow-up |
| `context_tokens` | `3200` | Budget for sources in the prompt |
| `preferences` | `true` | Use saved preferences as style guidance |
| **Logs, backups, cleanup** | | |
| `log_days` | `30` | Keep metrics and traces this long |
| `trace_sample` | `0.1` | Share of normal traces kept (errors and slow ones always kept) |
| `slow_trace_ms` | `2000` | A trace slower than this is always kept |
| `backup_every` | `daily` | `daily` or `off` |
| `backup_keep` | `7` | Scheduled backups to keep |
| `backup_documents` | `false` | Include documents in scheduled backups |
| `trash_days` / `export_days` | `30` / `7` | Clean-up ages |
| `update_check` | `false` | Check PyPI for a newer version (off unless you turn it on) |
| `log_level` | `info` | |

Test-only values: `llm=fake`, `embedder=fake`.

## API routes

All under `/api`. Every state-changing request needs the header `X-Sidelamp: 1`.

| Area | Method and route | Purpose |
|---|---|---|
| System | `GET /health` | Open. Is the app running? |
| | `GET /config` | Open. Version, library path, model, local/cloud, warnings, first-run flag |
| | `GET /settings`, `PUT /settings` | Read / change settings |
| | `POST /settings/test` | "Test connection" for the model |
| | `GET /setup/detect`, `POST /setup` | First run: detect Ollama, choose the library |
| Library | `GET /documents`, `POST /documents` | List / upload |
| | `GET /documents/{id}`, `DELETE /documents/{id}` | Details / move to Trash |
| | `POST /documents/{id}/reread`, `POST /documents/{id}/cancel` | Read again / cancel |
| | `GET /documents/{id}/file` | The original file |
| | `GET /documents/{id}/passages/{n}` | One passage (for the citation panel) |
| | `GET /library/tree` | Folder tree |
| | `POST /library/rebuild` | Rebuild everything |
| | `POST /library/samples` | Add the sample documents |
| Search | `POST /search` | Hybrid search with explanations |
| Chat | `GET /chats`, `POST /chats` | List / create |
| | `GET /chats/{id}`, `PATCH /chats/{id}`, `DELETE /chats/{id}` | Read / rename or change documents / delete |
| | `POST /chats/{id}/ask` | Ask (SSE stream, or JSON with `?stream=false`) |
| | `PUT /chats/{id}/messages/{mid}/feedback` | Thumbs up / down |
| | `GET /chats/{id}/export?format=md\|json\|pdf` | Export |
| Preferences | `GET /preferences`, `PUT /preferences` | Read / switch on or off |
| | `POST /preferences/items`, `PATCH`/`DELETE /preferences/items/{id}`, `DELETE /preferences/items` | Add, edit, delete, clear |
| | `POST /preferences/suggestions/{id}/save`, `DELETE /preferences/suggestions/{id}` | Save / dismiss a suggestion |
| Analysis | `GET /analysis/menu` | The fixed menu (for the builder) |
| | `GET /analysis/{doc_id}/overview` | Automatic overview |
| | `POST /analysis/run` | Run a plan (builder, no model) |
| | `POST /analysis/ask` | Question → plan (model) → computed answer |
| | `POST /analysis/compare` | Two CSVs side by side |
| System pages | `GET /system/overview`, `/system/metrics`, `/system/processing` | Dashboards |
| | `GET /system/traces`, `/system/traces/{id}` | Traces |
| | `GET /system/activity`, `/system/activity/check`, `/system/activity.csv` | Activity log |
| | `GET /system/update` | Update notice (only if turned on) |
| Evaluations | `GET /evals`, `POST /evals`, `GET /evals/{id}` | Runs |
| Data | `POST /exports`, `GET /exports/{id}`, `GET /exports/{id}/download` | Download everything |
| | `GET /backups`, `POST /backups` | List / create |
| | `POST /backups/{name}/verify`, `POST /backups/{name}/restore` | Verify / restore |

## Key decisions

- **Development happens in Claude's workspace and is synced to the repo for each commit.** It has
  Python 3.11 and fast package installs; your computer's shell has Python 3.10.
- **Strict CSP without breaking Next.js.** A static Next.js export needs a few inline scripts. The
  server hashes them when it loads the site and allows only those hashes. Styles allow
  `'unsafe-inline'` (low risk, documented).
- **The access token protects the API, not the static files.** The web app's files are public
  code, and a browser can't attach a header to a page load. Every API call needs the token.
- **External deletions:** if the library folder itself disappears (unplugged drive), the watcher
  pauses instead of treating every document as deleted.
- **Secrets live in the OS data folder**, so syncing the library never uploads API keys.
- **Test doubles:** a fake LLM and a fake (hashed bag-of-words) embedder, so tests run offline.
