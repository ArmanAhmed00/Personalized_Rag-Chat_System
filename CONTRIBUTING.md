# Contributing to Outlawz

Thanks for helping. Outlawz aims to be easy to read, easy to run and easy to change, so small,
focused contributions are the most welcome kind: a clearer message, a missing test, a new file
type, a new model provider, a new analysis.

Please read the [code of conduct](CODE_OF_CONDUCT.md). Report security problems privately, as
described in [SECURITY.md](SECURITY.md), not in a public issue.

## Setting up

You need Python 3.11+ and, for the web app, Node.js 20+. You don't need a model: the tests use a
fake model and a fake embedder, and run offline in temporary folders.

```bash
git clone https://github.com/ArmanAhmed00/Personalized_Rag-Chat_System.git outlawz
cd outlawz

# Backend
cd backend
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                          # about 30 seconds
ruff check . ../scripts && ruff format --check . ../scripts && mypy

# Web app
cd ../frontend
npm ci
npm run lint && npm run typecheck && npm run build

# Optional: run the same checks before every commit
pip install pre-commit && pre-commit install
```

To work on the web app with hot reload, run the backend and the Next.js dev server side by side:

```bash
OUTLAWZ_DEV_ORIGINS=http://localhost:3000 outlawz start --no-browser   # API on :8642
cd frontend && npm run dev                                               # UI on :3000
```

To try the full app with a fake model (handy for UI work), start it with
`OUTLAWZ_LLM=fake OUTLAWZ_EMBEDDER=fake OUTLAWZ_RERANKER=lexical`. To bundle the web app into the
Python package, run `python scripts/build_web.py`.

## Where things are

`backend/outlawz/` is the app (FastAPI), `frontend/` the web app (Next.js static export, served by
the Python app on the same port), `scripts/` the helper scripts, `docker/` the container setup and
`docs/` the documentation. [docs/architecture.md](docs/architecture.md) explains how the parts fit
together, and lists the extension points.

## Rules the code follows

These keep Outlawz private, safe and recoverable. A pull request that breaks one needs a very
good reason.

- **No database.** State is JSON, JSONL and `.npy` files. Everything derived (passages, vectors,
  the search index) can be rebuilt from the documents.
- **Crash-safe writes.** Write through the helpers in `store/files.py` (temporary file, fsync,
  rename). Never write a file in place.
- **Paths stay inside the library.** Resolve user-supplied paths with `store/safepath.py`.
- **Logs hold numbers and ids, never content.** No document text, questions, answers, file names
  or paths in metrics, traces or the activity log.
- **The server checks the model.** Citations are validated, analysis plans are validated against
  a fixed menu, and the model never writes code.
- **Versioned on-disk format.** If you change what's stored, bump `CURRENT_FORMAT` in
  `store/migrations.py`, add a migration (safe to run twice) and a test that upgrades a folder
  written in the old format.
- **Plain language.** Error messages say what happened and what to do, without jargon. All UI copy
  lives in `frontend/lib/strings.ts`.
- **Accessible UI.** Keyboard reachable, visible focus, labels on every control, and charts have a
  table view.
- **No telemetry, no new network calls** unless they're to the model the user chose, or are off by
  default and explained in Settings.

## Tests

Every change in behaviour comes with a test. Tests live in `backend/tests/`, use the fixtures in
`conftest.py` (a temporary library and data folder, the fake model and embedder) and must run
offline on Linux, macOS and Windows. Quality is measured too: `outlawz eval` runs a synthetic
benchmark with deterministic checks, and its results can gate CI with `--min` and `--max`.

## Pull requests

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Keep the change focused, and update docs, `.env.example` (for a new setting) and
   `CHANGELOG.md` (under "Unreleased").
3. Make sure CI passes: lint, types and tests on Linux, macOS and Windows; the web build; the
   packaged wheel; the Docker image; and the secret scan.

By contributing, you agree that your contribution is licensed under the [MIT licence](LICENSE).
