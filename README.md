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

By Arman Ahmed, Yahya Sow, Vishal Naveen. [MIT licence](LICENSE).

## Quick start

> [!IMPORTANT]
> **First, get a model.** Outlawz needs one to answer questions. Install
> [Ollama](https://ollama.com), start it, then download a model once:
>
> ```bash
> ollama pull llama3.1:8b      # about 5 GB; on an 8 GB laptop, llama3.2:3b is faster
> ```

Then pick **one** of the three ways below. Each ends with Outlawz open at
**<http://127.0.0.1:8642>**. The first visit walks you through choosing a library folder and a
model, and offers sample documents to try.

### Option 1: Docker (nothing else to install)

Needs [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine on Linux).

```bash
git clone https://github.com/ArmanAhmed00/Personalized_Rag-Chat_System.git outlawz
cd outlawz
mkdir -p docker/library                                # your documents go in this folder
docker compose -f docker/compose.yaml up -d --build    # build and start (a few minutes the first time)
```

| To | Run |
|---|---|
| **Open the app** | <http://127.0.0.1:8642> |
| **Check it's running** | `docker compose -f docker/compose.yaml ps` (shows `healthy`) |
| **See the logs** | `docker compose -f docker/compose.yaml logs -f` |
| **Stop** | `docker compose -f docker/compose.yaml down` (your documents and chats are kept) |
| **Start again** | `docker compose -f docker/compose.yaml up -d` |
| **Update** | `git pull` then `docker compose -f docker/compose.yaml up -d --build` |
| **Use another port** | `OUTLAWZ_PORT=8643 docker compose -f docker/compose.yaml up -d` |
| **Run Ollama in Docker too** | `OUTLAWZ_OLLAMA_URL=http://ollama:11434 docker compose -f docker/compose.yaml --profile ollama up -d`<br>then `docker compose -f docker/compose.yaml exec ollama ollama pull llama3.1:8b` |

The port is published on 127.0.0.1 only, and the app runs as an ordinary user (uid 1000).

### Option 2: Run script (no Docker)

Needs [Python 3.11+](https://www.python.org/downloads/) and [Node.js 20+](https://nodejs.org)
(Node is only used once, to build the web app).

**macOS and Linux**

```bash
git clone https://github.com/ArmanAhmed00/Personalized_Rag-Chat_System.git outlawz
cd outlawz
./scripts/run.sh
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/ArmanAhmed00/Personalized_Rag-Chat_System.git outlawz
cd outlawz
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # only if PowerShell blocks scripts
.\scripts\run.ps1
```

The first run sets up `backend/.venv`, installs Outlawz into it, builds the web app and opens your
browser. Later runs just start it.

| To | macOS and Linux | Windows |
|---|---|---|
| **Start** | `./scripts/run.sh` | `.\scripts\run.ps1` |
| **Stop** | <kbd>Ctrl</kbd>+<kbd>C</kbd> in that terminal | <kbd>Ctrl</kbd>+<kbd>C</kbd> in that terminal |
| **Use another port** | `./scripts/run.sh --port 9000` | `.\scripts\run.ps1 --port 9000` |
| **Update** | `git pull` then `backend/.venv/bin/python scripts/build_web.py` | `git pull` then `backend\.venv\Scripts\python scripts\build_web.py` |

<details>
<summary><b>Doing it by hand instead</b> (what the run script does)</summary>

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
python ../scripts/build_web.py     # builds the web app (needs Node.js 20+)
outlawz start
```

</details>

### Option 3: pipx (after the first release is on PyPI)

```bash
pipx install outlawz
outlawz start
```

### Commands

Once installed (Options 2 and 3), the `outlawz` command does everything else. With the run script,
use `backend/.venv/bin/outlawz` (Windows: `backend\.venv\Scripts\outlawz`), or activate the
environment first.

| Command | What it does |
|---|---|
| `outlawz start` | Start and open the browser |
| `outlawz start --port 9000 --library ~/Notes --no-browser` | Another port, another library folder, no browser |
| `outlawz doctor` | Check the setup and say how to fix each problem |
| `outlawz backup` | Back up the library's app data (add `--documents` to include your files) |
| `outlawz verify <backup.tar.gz>` | Check a backup against its manifest |
| `outlawz restore <backup.tar.gz>` | Restore a backup (stop Outlawz first) |
| `outlawz eval` | Run the evaluation benchmark |
| `outlawz --help` | Every command and option |

### If something goes wrong

| Message | Fix |
|---|---|
| `Ollama doesn't have the model "llama3.1:8b"` | `ollama pull llama3.1:8b`, then click **Ask again** |
| `Ollama isn't answering` | Start the Ollama app (or `ollama serve`) |
| `Port 8642 is already in use` / `address already in use` | Find what uses it: `lsof -nP -iTCP:8642 -sTCP:LISTEN` (Windows: `netstat -ano \| findstr :8642`), stop it with `kill <PID>` (Windows: `taskkill /PID <PID> /F`), or use another port |
| `failed to connect to the docker API` | Start Docker Desktop (macOS: `open -a Docker`) and try again |
| Docker on Linux can't reach Ollama | Start Ollama with `OLLAMA_HOST=0.0.0.0 ollama serve`, or run Ollama in Docker (see the Docker table) |
| Anything else | `outlawz doctor` (Docker: `docker compose -f docker/compose.yaml logs`) |

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
