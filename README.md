# Outlawz

**Chat with your own documents, on your own computer. Every answer cites the page it came from.**

Outlawz reads your PDFs, EPUB ebooks and CSV spreadsheets, answers questions using only what they
say, and links every claim to the exact passage behind it. Questions about numbers in a CSV are
computed with real code on every row, never guessed by the model.

- **Local-first and private.** No accounts, no cloud services, no telemetry. Your documents stay
  ordinary files in a folder you choose. With a local model (Ollama by default), nothing leaves
  your computer. If you choose a cloud model, the app tells you so on every page.
- **Answers you can check.** Sources are numbered `[1]`, `[2]`… Click one to read the passage,
  then open the original file at that page. Citations that point at nothing are removed, and an
  answer without one is flagged.
- **Numbers that are right.** For CSVs, the model only picks an analysis from a fixed menu (as
  JSON). Pandas computes it on every row, and the summary is checked so it contains no number that
  wasn't computed. Results come with a chart, a table and a CSV download.
- **Runs on ordinary laptops.** 8 GB of RAM, no GPU. macOS, Windows and Linux.
- **Your data stays readable.** No database: JSON, JSONL and NumPy files in a hidden `.outlawz`
  folder inside your library. Everything there can be rebuilt from your documents, writes are
  crash-safe, and backups are checked against a SHA-256 manifest before a restore.

By Arman Ahmed. [MIT licence](LICENSE).

## Install

You need a model to answer questions. The easiest is [Ollama](https://ollama.com): install it,
then download a model once:

```bash
ollama pull llama3.1:8b
```

Then pick one way to run Outlawz.

### With pipx (recommended)

```bash
pipx install outlawz
outlawz start
```

Your browser opens at <http://127.0.0.1:8642>. The first run walks you through choosing a library
folder and a model, and offers sample documents to try. Python 3.11 or newer is needed;
[pipx](https://pipx.pypa.io) keeps Outlawz in its own environment. (Until the first release is on
PyPI, use Docker or a checkout below.)

### With Docker

```bash
git clone https://github.com/ArmanAhmed00/Personalized_Rag-Chat_System.git outlawz
cd outlawz
mkdir -p docker/library                      # your documents go here
docker compose -f docker/compose.yaml up -d --build
```

Open <http://127.0.0.1:8642>. The port is published on 127.0.0.1 only. Outlawz talks to Ollama
on your computer through `host.docker.internal`; to run Ollama in Docker too, see the comments in
[`docker/compose.yaml`](docker/compose.yaml). The app runs as an ordinary user (uid 1000), and
your library and the app's data live in volumes.

### From a checkout

```bash
./scripts/run.sh            # macOS and Linux
.\scripts\run.ps1           # Windows PowerShell
```

The first run sets up `backend/.venv`, installs Outlawz into it and, if Node.js 20+ is
installed, builds the web app. Later runs just start it.

## Using it

1. **Add documents.** Drop PDF, EPUB or CSV files into your library folder (subfolders are fine),
   or upload them in the Library page. Outlawz notices new, changed, moved and deleted files by
   itself. Scanned PDFs are read too if [Tesseract](https://tesseract-ocr.github.io) is installed.
2. **Ask.** In Chat, ask a question. Choose which documents to search, or search them all.
   Follow-up questions work. Stop an answer at any time.
3. **Check.** Click any `[n]` to see the passage, and open the document at that page.
4. **Analyse a table.** Ask a chat question about a CSV ("total revenue by region per month"), or
   open Analysis for an automatic overview, a form-based builder and a side-by-side comparison of
   two CSVs.

**Preferences.** Type "Remember that I prefer bullet points" (or "From now on…") and Outlawz
offers to save it. Saved preferences shape how answers are written; they're never used as evidence.

**Search** finds passages by meaning, by exact words, or both, and explains why each result is
there.

**Export and back up.** Export any chat as Markdown, JSON or PDF. "Download everything" builds a
ZIP of your documents, chats, preferences and settings (API keys are never included). Backups can
run daily, and every backup is verified before it's restored.

**System pages** show what's being read, requests and timings, step-by-step traces, an evaluation
benchmark, and a tamper-evident activity log. Logs hold numbers and ids only, never document
text, questions or answers.

## Models

| Use | Default | Other choices |
|---|---|---|
| Answers | Ollama, `llama3.1:8b` | Any OpenAI-compatible server (LM Studio, llama.cpp, vLLM), the OpenAI API, Anthropic |
| Search | `BAAI/bge-small-en-v1.5` via FastEmbed (runs on the CPU, about 70 MB, downloaded once) | Ollama embeddings |
| Reranking | `Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder | Word overlap, or off |

Change them in Settings (with a "Test connection" button) or with environment variables. API keys
are stored on your computer only (`secrets.json` in the app's data folder), never in the library,
in exports or in backups.

## Settings

Everything is in the Settings page. Each setting can also be fixed with an environment variable
`OUTLAWZ_<NAME>` (for example `OUTLAWZ_PORT=9000`); the page then shows it as locked.
[`.env.example`](.env.example) lists every setting with its default and what it does.

Useful commands:

```bash
outlawz start --port 9000 --library ~/Notes   # another port or library
outlawz doctor                                # check the setup and say how to fix problems
outlawz backup --documents                    # back up the library
outlawz verify <backup>                       # check a backup against its manifest
outlawz restore <backup>                      # restore (Outlawz must not be running)
outlawz eval                                  # run the evaluation benchmark
```

## Privacy and security

- Outlawz listens on 127.0.0.1 only. To use it from another device, set an access token
  (`OUTLAWZ_ACCESS_TOKEN`) and put it behind HTTPS; without a token, requests from other
  addresses are refused.
- Every state-changing request needs a custom header, so other websites can't drive the app from
  your browser. Unknown `Host` headers are refused.
- The web app is served with a strict Content-Security-Policy: no inline script except the exact
  ones the build produced (by SHA-256 hash), no `eval`.
- Every file path is checked to stay inside the library.
- No telemetry. The only network calls are to the model you chose and, if you turn it on, a daily
  version check with pypi.org.

See [SECURITY.md](SECURITY.md) to report a problem.

## How it works

PDF, EPUB and CSV files are split into passages that remember their page, chapter and section (and
CSV rows). Search combines BM25 exact-word scores with vector similarity using reciprocal rank
fusion, then reranks the best candidates. The model gets the numbered passages and must cite them;
the server checks every citation. CSV questions go to a fixed menu of analyses run with pandas.
[docs/architecture.md](docs/architecture.md) has the details.

## Contributing

Contributions are welcome: bug reports, documentation, tests, new file types, model providers and
analyses. Start with [CONTRIBUTING.md](CONTRIBUTING.md). Tests run offline, with temporary folders
and a fake model, so you don't need a model to work on Outlawz.

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest && ruff check . ../scripts && ruff format --check . ../scripts && mypy
cd ../frontend && npm ci && npm run lint && npm run typecheck && npm run build
```

[ROADMAP.md](ROADMAP.md) lists what's planned, and [CHANGELOG.md](CHANGELOG.md) what changed.
Please follow the [code of conduct](CODE_OF_CONDUCT.md).
